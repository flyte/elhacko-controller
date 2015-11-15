import sys
import logging
import argparse
import traceback
from time import sleep

import zmq
import zerorpc
import crochet
from transitions import Machine
from transitions import logger

crochet.setup()  # Must be called before any twisted/autobahn imports
from autobahn.twisted.wamp import Application


logger.setLevel(logging.INFO)
ws_app = Application()


@crochet.run_in_reactor
def start_ws_app(ws_uri, ws_realm):
    ws_app.run(ws_uri, ws_realm, start_reactor=False)


@crochet.wait_for(timeout=1)
def ws_publish(topic, *args, **kwargs):
    """
    Publish to a topic on the websocket server.
    """
    return ws_app.session.publish(topic, *args, **kwargs)


class ElhackoController:

    states = [
        "awaiting_uuid",
        "have_uuid",
        "countdown",
        "have_img",
        "img_saved",
        "img_sent"
    ]
    transitions = [
        dict(trigger="got_uuid", source="awaiting_uuid", dest="have_uuid"),
        dict(trigger="started_countdown", source="have_uuid", dest="countdown"),
        dict(trigger="got_img", source="countdown", dest="have_img"),
        dict(trigger="saved_img", source="have_img", dest="img_saved"),
        dict(trigger="sent_img", source="img_saved", dest="img_sent"),
        dict(trigger="reset", source="*", dest="awaiting_uuid")
    ]
    sm = None
    uuid = None
    img_data = None

    def __init__(self, qr_uri, qr_prefix, img_cap_uri, ws_uri, ws_realm):
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

        # Image Capture set up
        self.img_cap_uri = img_cap_uri
        c = zerorpc.Client()
        c.connect(img_cap_uri) 
        self.img_cap_client = c

        # Websocket set up
        self.ws_uri = ws_uri
        self.ws_realm = ws_realm

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
        ws_publish(self.uuid, "COUNTDOWN")
        self.started_countdown()

    def get_img(self):
        """
        Orders the image capture service to take a picture and retrieves the image.
        """
        for i in range(3):
            logger.info(3-i)
            sleep(1)
        logger.info("Say cheese!")
        self.img_data = self.img_cap_client.take_photo()
        self.got_img()

    def save_img(self):
        sleep(1)
        self.saved_img()

    def send_img(self):
        ws_publish(self.uuid, "Here is the picture..")
        self.sent_img()

    def reset(self):
        self.uuid = None
        self.img_data = None


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("qr_uri")
    p.add_argument("qr_prefix")
    p.add_argument("img_cap_uri")
    p.add_argument("ws_uri", type=unicode)
    p.add_argument("ws_realm", type=unicode)
    args = p.parse_args()

    controller = ElhackoController(
        args.qr_uri,
        args.qr_prefix,
        args.img_cap_uri,
        args.ws_uri,
        args.ws_realm
    )
    start_ws_app(args.ws_uri, args.ws_realm)

    while True:
        try:
            controller.get_uuid()
            controller.start_countdown()
            controller.get_img()
            controller.save_img()
            controller.send_img()
        except KeyboardInterrupt:
            sys.exit(0)
        except Exception as e:
            logger.error("Exception: %s" % e)
            logger.error(traceback.format_exc())
        finally:
            controller.reset()
