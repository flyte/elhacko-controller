import sys
import os
import logging
import argparse
import traceback
import json
from time import sleep
from ConfigParser import SafeConfigParser
from io import BytesIO
from ftplib import FTP
from time import time
from ast import literal_eval

import zmq
import zerorpc
import crochet
import requests
from transitions import Machine
from transitions import logger

crochet.setup()  # Must be called before any twisted/autobahn imports
from autobahn.twisted.wamp import Application


CONFIG_SECTION = "elhacko"

logger.setLevel(logging.INFO)
ws_app = Application()


def get_config(path):
    """
    Read the configuration from an ini file and return the configuration in a dict.
    """
    cp = SafeConfigParser()
    cp.read(path)
    options = ("qr_uri qr_prefix img_cap_uri ws_uri ws_realm db_uri ftp_host ftp_user ftp_pass "
               "ftp_path img_srv_uri original_img_path img_countdown_secs compositor_uri "
               "composited_img_path composite_enabled".split())
    return {option: unicode(cp.get(CONFIG_SECTION, option)) for option in options}


@crochet.run_in_reactor
def start_ws_app(ws_uri, ws_realm):
    ws_app.run(ws_uri, ws_realm, start_reactor=False)


@crochet.wait_for(timeout=1)
def ws_publish(topic, data, *args, **kwargs):
    """
    Publish to a topic on the websocket server.
    """
    return ws_app.session.publish(topic, json.dumps(data), *args, **kwargs)


def store_file(host, user, pw, fp, directory, filename):
    """
    Store a file on an FTP server.
    """
    ftp = FTP(host, user, pw)
    ftp.cwd(directory)
    ftp.storbinary("STOR %s" % filename, fp)


class ElhackoController:

    states = [
        "awaiting_uuid",
        "have_uuid",
        "countdown",
        "have_img",
        "img_saved",
        "img_composited",
        "img_sent"
    ]
    transitions = [
        dict(trigger="got_uuid", source="awaiting_uuid", dest="have_uuid"),
        dict(trigger="started_countdown", source="have_uuid", dest="countdown"),
        dict(trigger="got_img", source="countdown", dest="have_img"),
        dict(trigger="saved_img", source="have_img", dest="img_saved"),
        dict(trigger="composited_img", source="img_saved", dest="img_composited"),
        dict(trigger="sent_img", source=("img_saved", "img_composited"), dest="img_sent"),
        dict(trigger="reset", source="*", dest="awaiting_uuid")
    ]
    sm = None
    uuid = None
    img_data = None
    img_uri = None

    def __init__(
        self, qr_uri, qr_prefix, img_cap_uri, ws_uri, ws_realm, db_uri,
        ftp_host, ftp_user, ftp_pass, ftp_path, img_srv_uri, original_img_path,
        img_countdown_secs, compositor_uri, composited_img_path, *args, **kwargs):
        self.machine = Machine(
            model=self,
            states=ElhackoController.states,
            initial="awaiting_uuid",
            transitions=ElhackoController.transitions
        )

        self.context = zmq.Context()

        # QR reader set up
        s = self.context.socket(zmq.SUB)
        s.connect(qr_uri)
        s.setsockopt_string(zmq.SUBSCRIBE, unicode(qr_prefix))
        self.qr_socket = s
        self.qr_prefix = qr_prefix

        # Countdown set up
        self.img_countdown_secs = int(img_countdown_secs)

        # Image Capture set up
        self.img_cap_uri = img_cap_uri
        c = zerorpc.Client()
        c.connect(img_cap_uri) 
        self.img_cap_client = c

        # Websocket set up
        self.ws_uri = ws_uri
        self.ws_realm = ws_realm

        # Database set up
        self.db_uri = db_uri

        # FTP set up
        self.ftp_host = ftp_host
        self.ftp_user = ftp_user
        self.ftp_pass = ftp_pass
        self.ftp_path = ftp_path
        self.img_srv_uri = img_srv_uri
        self.original_img_path = original_img_path

        # Compositor set up
        self.compositor_uri = compositor_uri
        self.composited_img_path = composited_img_path

    def get_uuid(self):
        """
        Waits for the QR reading service to produce a UUID.
        """
        self.uuid = self.qr_socket.recv()[len(self.qr_prefix):]
        logger.info("Got UUID: %s" % self.uuid)
        self.got_uuid()

    def start_countdown(self):
        """
        Publishes the "countdown" message to the websocket server on the uuid channel.
        """
        ws_publish(self.uuid, dict(status="countdown_started", seconds=self.img_countdown_secs))
        self.started_countdown()

    def get_img(self):
        """
        Orders the image capture service to take a picture and retrieves the image.
        """
        for i in range(self.img_countdown_secs):
            logger.info(self.img_countdown_secs-i)
            sleep(1)
        logger.info("Say cheese!")
        self.img_data = self.img_cap_client.take_photo()
        self.got_img()

    def save_img(self):
        """
        FTP the image to the webserver and set self.img_uri to the path at which the image is served
        """
        img = BytesIO(self.img_data["bytes"])
        filename = "%s.%s" % (str(int(time())), self.img_data["ext"])
        directory = os.path.join(self.ftp_path, self.original_img_path)
        store_file(self.ftp_host, self.ftp_user, self.ftp_pass, img, directory, filename)
        self.img_uri = "%s/%s/%s" % (self.img_srv_uri, self.original_img_path, filename)
        self.saved_img()

    def composite_img(self):
        """
        Send the image to the compositor to rekify it, then save the new img to FTP.
        """
        r = requests.post(
            self.compositor_uri,
            data=dict(img=self.img_uri, template="get-rekt"),
            stream=True
        )
        if r.status_code != 200:
            raise Exception("Compositor returned %s:\n%s" % (r.status_code, r.text))
        r.raw.decode_content = True
        directory = os.path.join(self.ftp_path, self.composited_img_path)
        filename = "composited-%s" % os.path.basename(self.img_uri)
        store_file(self.ftp_host, self.ftp_user, self.ftp_pass, r.raw, directory, filename)
        self.img_uri = "%s/%s/%s" % (self.img_srv_uri, self.composited_img_path, filename)
        self.composited_img()

    def send_img(self):
        """
        Publish the URL to the image on the websocket server.
        """
        ws_publish(self.uuid, dict(status="have_image", image=self.img_uri))
        self.sent_img()

    def reset(self):
        """ 
        Reset all variables to None.
        """
        self.uuid = None
        self.img_data = None
        self.img_uri = None
        self.composited_img_uri = None


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="elhacko.ini")
    args = p.parse_args()

    config = get_config(args.config)
    controller = ElhackoController(**config)
    start_ws_app(config["ws_uri"], config["ws_realm"])
    composite_enabled = literal_eval(config["composite_enabled"])

    while True:
        try:
            controller.get_uuid()
            controller.start_countdown()
            controller.get_img()
            controller.save_img()
            if composite_enabled:
                controller.composite_img()
            controller.send_img()
        except KeyboardInterrupt:
            sys.exit(0)
        except Exception as e:
            logger.error("Exception: %s" % e)
            logger.error(traceback.format_exc())
        finally:
            controller.reset()
