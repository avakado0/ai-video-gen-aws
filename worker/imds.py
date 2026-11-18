"""EC2 instance metadata (IMDSv2): the Spot interruption notice and the instance type.

The launch template sets the IMDS hop limit to 2 so a bridge-mode container can reach it
(spec 3E). A flaky metadata service must never look like an interruption, so every failure
here answers "no notice" / "unknown".
"""
import logging
import urllib.error
import urllib.request

log = logging.getLogger("worker.imds")


class Imds:
    def __init__(self, base_url="http://169.254.169.254", timeout=1.0):
        self.base_url = base_url
        self.timeout = timeout
        self._token = None

    def _request(self, method, path, headers=None):
        req = urllib.request.Request(self.base_url + path, method=method, headers=headers or {})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return resp.read().decode()

    def _get(self, path):
        """GET with a session token; fetch a fresh token once if the cached one was refused."""
        for attempt in (1, 2):
            if self._token is None:
                self._token = self._request("PUT", "/latest/api/token",
                                            {"X-aws-ec2-metadata-token-ttl-seconds": "21600"})
            try:
                return self._request("GET", path, {"X-aws-ec2-metadata-token": self._token})
            except urllib.error.HTTPError as e:
                if e.code == 401 and attempt == 1:
                    self._token = None
                    continue
                raise

    def spot_notice(self):
        """True once AWS has announced this Spot instance will be reclaimed (2 minutes ahead)."""
        try:
            self._get("/latest/meta-data/spot/instance-action")
            return True
        except urllib.error.HTTPError as e:
            if e.code != 404:
                log.warning("spot notice check: HTTP %s", e.code)
            return False
        except Exception as e:
            log.warning("spot notice check failed: %s", e)
            return False

    def instance_type(self):
        try:
            return self._get("/latest/meta-data/instance-type").strip()
        except Exception as e:
            log.warning("instance type lookup failed: %s", e)
            return "unknown"
