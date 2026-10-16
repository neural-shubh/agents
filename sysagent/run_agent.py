"""Entry point: python run_agent.py [daemon|--scan-junk|--scan-storage|--weekly|--health]

No argument = run the background daemon forever.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core import config, db  # noqa: E402


def main() -> None:
    config.ensure_dirs()
    db.init()
    args = set(sys.argv[1:])

    if "--scan-junk" in args:
        from modules import junk
        cfg = config.load()
        print(junk.clean(dry_run=cfg.get("dry_run", True)))
    elif "--scan-storage" in args:
        from modules import storage
        print(storage.scan())
    elif "--weekly" in args:
        from modules import devclean, duplicates, largefiles, startup, uninstall
        for fn in (largefiles.scan, duplicates.scan, devclean.scan, startup.scan,
                   uninstall.scan):
            print(fn.__module__, fn())
    elif "--health" in args:
        from modules import health
        print(health.report())
    else:
        from core import daemon
        daemon.run()


if __name__ == "__main__":
    main()
