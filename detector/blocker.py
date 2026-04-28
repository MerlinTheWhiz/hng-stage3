import logging
import subprocess


class IptablesBlocker:
    def __init__(self, chain="INPUT", enabled=True):
        self.chain = chain
        self.enabled = enabled

    def block(self, ip):
        if not self.enabled:
            logging.info("blocking disabled, skipping iptables ban for %s", ip)
            return True

        if self._has_rule(ip):
            return True

        return self._run(["iptables", "-I", self.chain, "-s", ip, "-j", "DROP"])

    def unblock(self, ip):
        if not self.enabled:
            logging.info("blocking disabled, skipping iptables unban for %s", ip)
            return True

        while self._has_rule(ip):
            if not self._run(["iptables", "-D", self.chain, "-s", ip, "-j", "DROP"]):
                return False
        return True

    def _has_rule(self, ip):
        if not self.enabled:
            return False
        return self._run(
            ["iptables", "-C", self.chain, "-s", ip, "-j", "DROP"],
            check=False,
        )

    def _run(self, command, check=True):
        try:
            result = subprocess.run(
                command,
                check=check,
                capture_output=True,
                text=True,
            )
            return result.returncode == 0
        except FileNotFoundError:
            logging.error("iptables binary not found")
            return False
        except subprocess.CalledProcessError as exc:
            logging.warning("iptables command failed: %s", exc.stderr.strip())
            return False
