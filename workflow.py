from datetime import datetime

import cacd_mailer
import caed_mailer
import frinkiac


def main():
    gif = frinkiac.get_gif()
    if datetime.today().weekday() == 0:  # Monday
        caed_mailer.main(gif=gif)
    cacd_mailer.main(gif=gif)

if __name__ == "__main__":
    main()