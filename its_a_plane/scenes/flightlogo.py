import os
from PIL import Image

from its_a_plane.utilities.animator import Animator
from its_a_plane.setup import colours

LOGO_SIZE = 16
DEFAULT_IMAGE = "default"
DIR_PATH = os.path.dirname(os.path.realpath(__file__))
LOGO_PATH = os.path.join(DIR_PATH, "../data/logo")
LOGO2_PATH = os.path.join(DIR_PATH, "../data/logo2")

class FlightLogoScene:
    @Animator.KeyFrame.add(0)
    def logo_details(self):

        # Guard against no data
        if len(self._data) == 0:
            return

        # Clear the whole area
        self.draw_square(
            0,
            0,
            LOGO_SIZE,
            LOGO_SIZE,
            colours.BLACK,
        )

        icao = self._data[self._data_index]["owner_icao"]
        if icao in ("", "N/A"):
            icao = DEFAULT_IMAGE

        # Open the file - try logo directory first, then logo2, then default
        try:
            image = Image.open(f"{LOGO_PATH}/{icao}.png")
        except FileNotFoundError:
            try:
                image = Image.open(f"{LOGO2_PATH}/{icao}.png")
            except FileNotFoundError:
                image = Image.open(f"{LOGO_PATH}/{DEFAULT_IMAGE}.png")


        # Make image fit our screen.
        try:
            resample = Image.Resampling.LANCZOS  # Pillow 10+
        except AttributeError:
            resample = Image.ANTIALIAS          # Pillow <10
        image.thumbnail((LOGO_SIZE, LOGO_SIZE), resample)
        self.matrix.SetImage(image.convert('RGB'))
