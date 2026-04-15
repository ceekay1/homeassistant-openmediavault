"""OpenMediaVault API."""

import json
import logging
from os import path
from typing import Any
from pickle import dump as pickle_dump
from pickle import load as pickle_load
from threading import Lock
from time import time

import requests
from voluptuous import Optional

_LOGGER = logging.getLogger(__name__)


# ---------------------------
#   load_cookies
# ---------------------------
#def load_cookies(filename: str) -> Optional(dict):
#    """Load cookies from file."""
#    if path.isfile(filename):
#        with open(filename, "rb") as f:
#            return pickle_load(f)
#    return None
# OMV7 fix


# ---------------------------
#   save_cookies
# ---------------------------
#def save_cookies(filename: str, data: dict):
#    """Save cookies to file."""
#    with open(filename, "wb") as f:
#        pickle_dump(data, f)
# OMV7 fix

# ---------------------------
#   OpenMediaVaultAPI
# ---------------------------
class OpenMediaVaultAPI(object):
    """Handle all communication with OMV."""

    def __init__(self, hass, host, username, password, use_ssl=False, verify_ssl=True):
        """Initialize the OMV API."""
        self._hass = hass
        self._host = host
        self._use_ssl = use_ssl
        self._username = username
        self._password = password
        self._protocol = "https" if self._use_ssl else "http"
        self._ssl_verify = verify_ssl
        if not self._use_ssl:
            self._ssl_verify = True
        self._resource = f"{self._protocol}://{self._host}/rpc.php"

        self.lock = Lock()

        self._connection = None
#        self._cookie_jar = None
#        self._cookie_jar_file = self._hass.config.path(".omv_cookies.json")
# OMV7 fix
        self._connected = False
        self._reconnected = False
        self._connection_epoch = 0
        self._connection_retry_sec = 58
        self.error = None
        self.connection_error_reported = False
        self.accounting_last_run = None

    # ---------------------------
    #   has_reconnected
    # ---------------------------
    def has_reconnected(self) -> bool:
        """Check if API has reconnected."""
        if self._reconnected:
            self._reconnected = False
            return True

        return False

    # ---------------------------
    #   connection_check
    # ---------------------------
    def connection_check(self) -> bool:
        """Check if API is connected."""
        if not self._connected or self._connection is None:
            if self._connection_epoch > time() - self._connection_retry_sec:
                return False

            if not self.connect():
                return False

        return True

    # ---------------------------
    #   disconnect
    # ---------------------------
    def disconnect(self, location="unknown", error=None):
        """Disconnect API."""
        if not error:
            error = "unknown"

        if not self.connection_error_reported:
            if location == "unknown":
                _LOGGER.error("OpenMediaVault %s connection closed", self._host)
            else:
                _LOGGER.error(
                    "OpenMediaVault %s error while %s : %s", self._host, location, error
                )

            self.connection_error_reported = True

        self._reconnected = False
        self._connected = False
        self._connection = None
        self._connection_epoch = 0

    # ---------------------------
    #   connect
    # ---------------------------
    def connect(self) -> bool:
        self.error = None
        self._connected = False
        self._connection_epoch = time()
        
        # No cookies
        self._connection = requests.Session() 
        
        # OMV 7 standard header
        self._connection.headers.update({
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        })

        try:
            _LOGGER.debug("Try login to OMV 7 at %s", self._resource)
            response = self._connection.post(
                self._resource,
                timeout=10,
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": f"{self._protocol}://{self._host}/",
                    "Origin": f"{self._protocol}://{self._host}",
                    "Content-Type": "application/json",
                },
                json={
                    "service": "Session",
                    "method": "login",
                    "params": {
                        "username": self._username,
                        "password": self._password,
                    },
                },
                verify=self._ssl_verify,
            )

            data = response.json()
            if data.get("response") and data["response"].get("authenticated"):
                self._connected = True
                _LOGGER.info("Successfully connected to OMV 7 (Host: %s)", self._host)
                return True
            else:
                _LOGGER.error("Rejected OMV 7 login: %s", data.get("error"))
                return False
        except Exception as e:
            _LOGGER.error("Error connecting to OMV 7: %s", e)
            return False

    # ---------------------------
    #   error_to_strings
    # ---------------------------
    def error_to_strings(self, error=""):
        """Translate error output to error string."""
        self.error = "cannot_connect"
        if "Incorrect username or password" in error:
            self.error = "wrong_login"

        if "certificate verify failed" in error:
            self.error = "ssl_verify_failed"

    # ---------------------------
    #   connected
    # ---------------------------
    def connected(self) -> bool:
        """Return connected boolean."""
        return self._connected

    # ---------------------------
    #   query
    # ---------------------------
    def query(
        self,
        service: str,
        method: str,
#        params: dict[str, Any] | None = {},
#        options: dict[str, Any] | None = {"updatelastaccess": True},
        params: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> Optional(list):
        """Retrieve data from OMV."""
        if params is None:
            params = {}
        if options is None:
            options = {"updatelastaccess": True}
        if not self.connection_check():
            return None

        self.lock.acquire()
        error = False
        try:
            _LOGGER.debug(
                "OpenMediaVault %s query: %s, %s, %s, %s",
                self._host,
                service,
                method,
                params,
                options,
            )
            response = self._connection.post(
                self._resource,
                timeout=10,
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": f"{self._protocol}://{self._host}/",
                    "Origin": f"{self._protocol}://{self._host}",
                    "Content-Type": "application/json",
                },
                json=
                    {
                        "service": service,
                        "method": method,
                        "params": params,
                        "options": options,
                    },
                verify=self._ssl_verify,
            )

            if response.status_code == 200:
                data = response.json()
                _LOGGER.debug("OpenMediaVault %s query response: %s", self._host, data)
            else:
                error = True

        except (
            requests.exceptions.ConnectionError,
            json.decoder.JSONDecodeError,
        ) as api_error:
            _LOGGER.warning("OpenMediaVault %s unable to fetch data", self._host)
            self.disconnect("query", api_error)
            self.lock.release()
            return None
        except Exception:
            self.disconnect("query")
            self.lock.release()
            return None

        # Socket errors
        if error:
            try:
                errorcode = response.status_code
            except Exception:
                errorcode = "no_respose"

            _LOGGER.warning(
                "OpenMediaVault %s unable to fetch data (%s)", self._host, errorcode
            )

            error_code = errorcode
            self.error = error_code
            self._connected = False
            self.lock.release()
            return None

        # Api errors
        if data is not None and data["error"] is not None:
            error_message = data["error"]["message"]
            error_code = data["error"]["code"]
            if (
                error_code == 5001
                or error_code == 5002
                or error_message == "Session not authenticated."
                or error_message == "Session expired."
            ):
                _LOGGER.debug("OpenMediaVault %s session expired", self._host)
                self.error = 5001
                if self.connect():
                    return self.query(service, method, params, options)

        self.error = None
        self.lock.release()

        return data["response"]
