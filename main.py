import signal
import sys


def signal_handler(sig, frame):
    print("\nShutting down gracefully...")
    sys.exit(0)


def main():
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        from src.op_secrets import load_secrets_from_1password
        loaded = load_secrets_from_1password()
        if loaded:
            print(f"Loaded {len(loaded)} secret(s) from 1Password: {', '.join(loaded.keys())}")

        from src.bot import run_bot
        run_bot()
    except ValueError as e:
        print(f"Configuration Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Fatal Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
