"""Shared pacing policy for the physically accepted classic-iPod writers."""
import time


CLASSIC_USB_SETTLE_SECONDS = 0.15


def pace_classic_usb() -> None:
    """Give validated classic hardware a short breath between media writes."""
    time.sleep(CLASSIC_USB_SETTLE_SECONDS)
