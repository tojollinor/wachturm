"""Publish a versioned update only from the latest successfully checked main."""
import base64
import json
import os
import re
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def publish_release(api, sha):
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Invalid checked commit")
    if api("GET", "git/ref/heads/main")["object"]["sha"] != sha:
        return "Skipped superseded commit"
    manifest = api("GET", f"contents/custom_components/ha_alarmzentrale/manifest.json?ref={sha}")
    manifest = json.loads(base64.b64decode(manifest["content"]))
    version = manifest["version"]
    if manifest["domain"] != "ha_alarmzentrale" or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Release needs the Wachturm domain and a stable version")
    tag = "v" + version
    if api("GET", "releases/tags/" + tag, optional=True) is not None:
        return "Already published: " + tag
    notes = api("GET", f"contents/docs/releases/{version}.md?ref={sha}")
    body = base64.b64decode(notes["content"]).decode("utf-8")
    release = api("POST", "releases", data={
        "tag_name": tag, "target_commitish": sha, "name": "Wachturm " + version,
        "body": body, "draft": False, "prerelease": False, "make_latest": "true",
    })
    return "Published: " + release["html_url"]


def main():
    event = json.loads(open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8").read())
    run = event["workflow_run"]
    repository = os.environ["GITHUB_REPOSITORY"]
    if (run["conclusion"] != "success" or run["head_branch"] != "main" or
            run["event"] != "push" or run["head_repository"]["full_name"] != repository):
        raise ValueError("Only successful trusted main checks can publish")
    token = os.environ["GH_TOKEN"]

    def api(method, path, data=None, optional=False):
        request = Request("https://api.github.com/repos/" + repository + "/" + path,
                          data=None if data is None else json.dumps(data).encode(), method=method,
                          headers={"Authorization": "Bearer " + token,
                                   "Accept": "application/vnd.github+json",
                                   "Content-Type": "application/json", "User-Agent": "Wachturm-release"})
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as exc:
            if optional and exc.code == 404:
                return None
            raise RuntimeError(f"GitHub API failed ({exc.code})") from None

    print(publish_release(api, run["head_sha"]))


if __name__ == "__main__":
    main()
