import argparse
from app.tests.unit import executive_token


def test():
    print("Starting all test cases. \n")

    # Run in sequence
    executive_token.test()

    print("\nAll test cases finished successfully.")


## Argparse setup
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run all unit test cases")
    parser.add_argument("-test", action="store_true", help="Run all test cases")
    args = parser.parse_args()

    if args.test:
        test()
