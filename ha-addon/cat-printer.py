"""
One-off print from image file or text. Uses yhk_printer library.
Config from env: PRINTER_MAC, PRINTER_PORT, PRINTER_WIDTH, PRINTER_FONT.
"""
import sys

import PIL.Image

from yhk_printer import get_config, print_image, print_text, printer_session


def main():
    cfg = get_config()
    print(f"Connecting to printer {cfg['mac']}:{cfg['port']}...")

    with printer_session() as s:
        if len(sys.argv) > 1:
            arg = sys.argv[1]
            if arg == "--text" and len(sys.argv) > 2:
                text = " ".join(sys.argv[2:])
                print_text(s, text, font_size=65)
            else:
                img = PIL.Image.open(arg)
                print_image(s, img)
        else:
            img = PIL.Image.open("images/Turtle.jpg")
            print_image(s, img)

    print("Done.")


if __name__ == "__main__":
    main()
