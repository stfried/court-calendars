from datetime import datetime

import cacd_mailer
import caed_mailer


def main():
    if datetime.today().weekday() == 0:  # Monday
        caed_mailer.main()
    cacd_mailer.main()

if __name__ == "__main__":
    main()