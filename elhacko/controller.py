import logging
import argparse

import zmq
import zerorpc
from transitions import Machine
from transitions import logger as sm_logger


sm_logger.addHandler(logging.StreamHandler())
sm_logger.setLevel(logging.INFO)


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

    def __init__(self, qr_uri, qr_prefix):
        self.machine = Machine(
            model=self,
            states=ElhackoController.states,
            initial="awaiting_uuid",
            transitions=ElhackoController.transitions
        )
        
        self.context = zmq.Context()
        s = self.context.socket(zmq.SUB)
        s.connect(qr_uri)
        s.setsockopt_string(zmq.SUBSCRIBE, unicode(qr_prefix))
        self.qr_socket = s
        self.qr_prefix = qr_prefix

    def get_uuid(self):
        """
        Waits for the QR reading service to produce a UUID.
        """
        self.uuid = self.qr_socket.recv()[len(self.qr_prefix):]
        sm_logger.info("Got UUID: %s" % self.uuid)
        self.got_uuid()

    def start_countdown(self):
        self.started_countdown()

    def get_img(self):
        self.got_img()

    def save_img(self):
        self.saved_img()

    def send_img(self):
        self.sent_img()

    def reset(self):
        self.uuid = None
        # self.reset()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("qr_uri")
    p.add_argument("qr_prefix")
    args = p.parse_args()

    controller = ElhackoController(args.qr_uri, args.qr_prefix)

    while True:
        try:
            controller.get_uuid()
            controller.start_countdown()
            controller.get_img()
            controller.save_img()
            controller.send_img()
        finally:
            controller.reset()