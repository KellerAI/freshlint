import sys


def _run() -> int:
    if sys.argv[1:2] == ["hook"]:
        # A hook must never fail a session, even when freshlint itself cannot be imported.
        try:
            from freshlint.hooks import main as hook_main

            return hook_main()
        except Exception:
            return 0
    from freshlint.cli import main

    return main()


sys.exit(_run())
