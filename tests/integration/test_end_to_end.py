# To record this cassette (one-time, manual setup):
#
#   1. Pick a tiny PUBLIC Wattpad story (1-2 chapters), preferably one you don't
#      mind having visible in the cassette file forever.
#   2. Replace REPLACE_ME_AFTER_RECORDING below with that story's numeric ID.
#   3. Run:  pytest tests/integration/test_end_to_end.py --record-mode=once
#   4. Review tests/integration/cassettes/*.yaml — confirm no Cookie/Authorization
#      headers and no PII leaked through. If you ran this with a real session
#      cookie configured, scrub it before committing.
#   5. Commit the cassette.
#
# After recording, remove the pytest.mark.skip decorator below.

from pathlib import Path

import pytest

from local_story_archive.archive.state import Manifest
from local_story_archive.client import RateLimitedClient
from local_story_archive.config import Config
from local_story_archive.jobs import archive_story

pytestmark = pytest.mark.skip(
    reason="Cassette not yet recorded; record per instructions in this file."
)


@pytest.mark.vcr(cassette_library_dir="tests/integration/cassettes")
def test_archive_one_real_story_from_cassette(output_dir: Path):
    cfg = Config(output_dir=output_dir, rate_limit_per_sec=1000.0)
    client = RateLimitedClient(cfg)
    manifest = Manifest(output_dir).connect()
    try:
        # Replace this once a cassette is recorded for a known small public story.
        archive_story(cfg, client, manifest, "REPLACE_ME_AFTER_RECORDING")
    finally:
        manifest.close()
        client.close()

    # Generic structural assertions:
    stories = list((output_dir / "stories").glob("*/*"))
    assert len(stories) == 1
    sd = stories[0]
    assert (sd / "metadata.json").exists()
    assert any((sd / "parts").glob("*.txt"))
    assert any((sd / "output").glob("*.epub"))
