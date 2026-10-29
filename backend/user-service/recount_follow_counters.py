"""Find - and, with --apply, correct - profiles whose stored follower and
following counts no longer match the follows that exist.

    docker exec -w /app kaluta-user python recount_follow_counters.py          # dry run: report only
    docker exec -w /app kaluta-user python recount_follow_counters.py --apply  # rewrite the drifted counts

profiles.followers_count / following_count are a cache of the follows table,
moved only by the follow and unfollow buttons. Blocking (until 29/10) and the
26/10 member rebuild deleted follows without moving them, so a profile could
say more followers than it has - and disagree with "Most followed on Kinjy",
which counts the follows themselves. This recounts from the follows table and
touches nothing else: no follow is added or removed. Running it again finds
nothing more to do.
"""
from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import func, select

from common.database import SessionLocal

import models


def run(apply: bool) -> dict:
    report: dict = {"mode": "apply" if apply else "dry-run", "drifted": [], "corrected": 0}
    with SessionLocal() as db:
        followers = dict(db.execute(select(models.Follow.followee_id, func.count()).group_by(models.Follow.followee_id)).all())
        following = dict(db.execute(select(models.Follow.follower_id, func.count()).group_by(models.Follow.follower_id)).all())
        for profile in db.scalars(select(models.Profile)).all():
            real = (followers.get(profile.user_id, 0), following.get(profile.user_id, 0))
            stored = (profile.followers_count or 0, profile.following_count or 0)
            if real == stored:
                continue
            report["drifted"].append({"user_id": profile.user_id, "handle": profile.handle,
                                      "followers": {"stored": stored[0], "real": real[0]},
                                      "following": {"stored": stored[1], "real": real[1]}})
            if apply:
                profile.followers_count, profile.following_count = real
                report["corrected"] += 1
        if apply:
            db.commit()
    report["count"] = len(report["drifted"])
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--apply", action="store_true", help="rewrite the drifted counts (default: report only)")
    print(json.dumps(run(parser.parse_args().apply), indent=2))
    sys.exit(0)
