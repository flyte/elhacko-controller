from transitions import Machine


class ElhackoController:

    states = (
        "awaiting_qr",
        "have_qr",
        "countdown",
        "have_img",
        "img_saved",
        "img_sent"
    )

    transitions = (
        dict(trigger="get_qr", source="awaiting_qr", dest="have_qr"),
        dict(trigger="send_countdown", source="have_qr", dest="countdown"),
        dict(trigger="get_img", source="countdown", dest="have_img"),
        dict(trigger="save_img", source="have_img", dest="img_saved"),
        dict(trigger="send_img", source="img_saved", dest="img_sent"),
        dict(trigger="reset", source="img_sent", dest="awaiting_qr")
    )