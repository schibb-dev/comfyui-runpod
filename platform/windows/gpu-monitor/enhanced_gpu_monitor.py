#!/usr/bin/env python3
"""
Enhanced GPU Monitoring and Failure Detection System
Extends the auto queue manager with comprehensive GPU failure logging
"""

import requests
import json
import time
import os
import sys
import threading
import atexit
import subprocess
import re
import psutil
import argparse
from datetime import datetime, timedelta
from pathlib import Path
import logging
from logging.handlers import TimedRotatingFileHandler
from typing import Dict, List, Optional, Tuple

# Optional Windows-specific imports
try:
    import winreg
    import win32evtlog
    import win32evtlogutil

    WINDOWS_EVENT_LOG_AVAILABLE = True
except ImportError:
    WINDOWS_EVENT_LOG_AVAILABLE = False


class EnhancedGPUMonitor:
    def __init__(self, log_file=None, report_only=None, config_file=None):
        # Load config first (persistent settings)
        config = self._load_config(config_file)

        # Use config values, but allow parameter overrides
        self.log_file = log_file or config.get("log_file", "C:/Logs/gpu_crash.log")
        # Command-line override takes precedence over config
        if report_only is None:
            self.report_only = config.get("report_only", True)
        else:
            self.report_only = report_only

        today_stamp = datetime.now().strftime("%Y-%m-%d")
        base, ext = os.path.splitext(self.log_file)
        # Keep main log without date stamp for consistency with existing logs
        self.csv_file = f"{base}-{today_stamp}.csv"
        self.failure_log = f"{base}-failures-{today_stamp}.json"

        # Load other config values
        self.max_unresponsive_threshold = config.get("failure_threshold", 3)
        # If false, transient nvidia-smi issues (timeouts / no-data / generic failures) won't count toward crash threshold.
        # This helps prevent false-positive restarts/reboots from brief NVML hiccups.
        self.count_transient_failures = bool(config.get("count_transient_failures", False))
        self.sampling_interval = config.get("sampling_interval", 10)
        self.enable_generation_logging = config.get("enable_generation_logging", True)
        self.generation_sampling_interval = config.get("generation_sampling_interval", 1)
        self.generation_utilization_threshold = config.get("generation_utilization_threshold", 20)
        self.power_limit = config.get("power_limit", None)  # None = don't set, or wattage value
        self.temperature_warning_threshold = config.get("temperature_warning_threshold", 80)
        self.temperature_critical_threshold = config.get("temperature_critical_threshold", 85)

        # Recovery ladder (used when GPU is lost)
        # IMPORTANT: if report_only=false and recovery steps include "reboot", this can reboot Windows.
        self.recovery_enabled = config.get("recovery_enabled", True)
        self.recovery_steps = config.get(
            "recovery_steps",
            [
                "restart_docker_containers",
                "wsl_shutdown",
                "restart_docker_service",
                "pnputil_restart_display",
                "reboot",
            ],
        )
        self.recovery_fallback_to_reboot = config.get("recovery_fallback_to_reboot", True)
        self.recovery_docker_containers = config.get("recovery_docker_containers", [])
        self.recovery_docker_services = config.get(
            "recovery_docker_services", ["com.docker.service"]
        )
        self.recovery_verify_attempts = int(config.get("recovery_verify_attempts", 6))
        self.recovery_verify_interval_seconds = float(
            config.get("recovery_verify_interval_seconds", 5)
        )
        self.recovery_pnputil_restart_display = bool(
            config.get("recovery_pnputil_restart_display", False)
        )
        # Optional: provide an explicit instance id (Device Manager -> Display adapters -> Details -> Device instance path)
        self.recovery_pnputil_instance_id = config.get("recovery_pnputil_instance_id", None)

        # GPU monitoring state
        self.gpu_history = []
        self.failure_events = []
        self.last_gpu_state = None
        self.gpu_unresponsive_count = 0
        self.is_generating = False
        self._power_limit_check_counter = 0  # Counter for periodic power limit verification
        self._power_limit_permission_denied = False  # Track if we've been denied permissions

        # Monitoring threads
        self._monitor_thread = None
        self._stop_event = threading.Event()
        self._comfyui_log_thread = None

        # Register cleanup
        atexit.register(self._cleanup)
        self.setup_logging()

        # Set power limit if configured (after logging is set up)
        if self.power_limit is not None:
            self._set_power_limit(self.power_limit)

    def _load_config(self, config_file=None):
        """Load configuration from file (persistent settings)"""
        default_config = {
            "report_only": True,
            "log_file": "C:/Logs/gpu_crash.log",
            "sampling_interval": 10,
            "enable_generation_logging": True,
            "generation_sampling_interval": 1,
            "generation_utilization_threshold": 20,
            "failure_threshold": 3,
            # If false, transient nvidia-smi errors won't trigger crash escalation.
            "count_transient_failures": False,
            "temperature_warning_threshold": 80,
            "temperature_critical_threshold": 85,
            "power_warning_threshold": 95,
            "power_limit": None,  # Set to wattage (e.g., 150, 155, 160) to enable, None to disable
            "monitor_event_log": True,
            "monitor_comfyui_logs": True,
            # Recovery ladder (GPU lost)
            # Steps are tried in order. If all fail and recovery_fallback_to_reboot=true, we reboot (when report_only=false).
            "recovery_enabled": True,
            "recovery_steps": [
                "restart_docker_containers",
                "wsl_shutdown",
                "restart_docker_service",
                "pnputil_restart_display",
                "reboot",
            ],
            "recovery_fallback_to_reboot": True,
            # Only used for restart_docker_containers step
            "recovery_docker_containers": [],
            # Only used for restart_docker_service step
            "recovery_docker_services": ["com.docker.service"],
            # After each step, re-check GPU availability
            "recovery_verify_attempts": 6,
            "recovery_verify_interval_seconds": 5,
            # pnputil step (Windows 11): disabled by default because it may require admin and can be disruptive
            "recovery_pnputil_restart_display": False,
            "recovery_pnputil_instance_id": None,
        }

        # Try to find config file
        if config_file is None:
            config_candidates = [
                Path("C:/Logs/gpu_monitor_config.json"),
                Path(__file__).parent / "gpu_monitor_config.json",
                Path(__file__).parent.parent / "gpu_monitor_config.json",
            ]
            for candidate in config_candidates:
                if candidate.exists():
                    config_file = candidate
                    break

        if config_file and Path(config_file).exists():
            try:
                with open(config_file, "r", encoding="utf-8") as f:
                    user_config = json.load(f)
                    # Merge with defaults
                    default_config.update(user_config)
            except Exception as e:
                print(f"Warning: Could not load config from {config_file}: {e}", file=sys.stderr)

        return default_config

    def setup_logging(self):
        """Setup enhanced logging configuration"""
        os.makedirs(os.path.dirname(self.log_file), exist_ok=True)

        # Main log handler
        rotating_handler = TimedRotatingFileHandler(
            filename=self.log_file, when="midnight", backupCount=7, encoding="utf-8"
        )

        # Failure-specific handler
        failure_handler = TimedRotatingFileHandler(
            filename=self.failure_log.replace(".json", ".log"),
            when="midnight",
            backupCount=30,  # Keep more failure logs
            encoding="utf-8",
        )

        # Match existing log format: [YYYY-MM-DD HH:MM:SS] [LEVEL] message
        formatter = logging.Formatter(
            "[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
        rotating_handler.setFormatter(formatter)
        failure_handler.setFormatter(formatter)

        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)

        # Setup loggers
        self.logger = logging.getLogger("gpu_monitor")
        self.logger.setLevel(logging.INFO)
        self.logger.addHandler(rotating_handler)
        self.logger.addHandler(console_handler)

        self.failure_logger = logging.getLogger("gpu_failures")
        self.failure_logger.setLevel(logging.WARNING)
        self.failure_logger.addHandler(failure_handler)

    def _to_float(self, value: Optional[str]) -> float:
        """Parse a float from strings like '163.11 W', '97 %', '12908 MiB'.
        Returns NaN if no numeric token is found.
        """
        try:
            if value is None:
                return float("nan")
            s = str(value)
            # Find the first numeric token (supports integers and decimals, optional sign)
            match = re.search(r"[-+]?\d*\.?\d+", s)
            if match:
                return float(match.group(0))
            return float("nan")
        except Exception:
            return float("nan")

    def _find_nvidia_smi(self):
        """Enhanced nvidia-smi detection with fallback paths"""
        candidates = [
            "nvidia-smi",
            r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
            r"C:\Windows\System32\nvidia-smi.exe",
            r"C:\Windows\Sysnative\nvidia-smi.exe",
            r"C:\Program Files\NVIDIA Corporation\NVIDIA GeForce Experience\NVSMI\nvidia-smi.exe",
        ]

        for path in candidates:
            try:
                if path == "nvidia-smi":
                    result = subprocess.run(
                        ["where", "nvidia-smi"], capture_output=True, text=True, shell=True
                    )
                    if result.returncode == 0:
                        return "nvidia-smi"
                else:
                    if os.path.exists(path):
                        return path
            except Exception:
                continue
        return None

    def _run_command(
        self, args: List[str], timeout: int = 60, shell: bool = False
    ) -> Tuple[bool, str]:
        """Run a command and return (ok, combined_output)."""
        try:
            r = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=shell,
            )
            out = (r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")
            return r.returncode == 0, out.strip()
        except FileNotFoundError as e:
            return False, str(e)
        except subprocess.TimeoutExpired:
            return False, f"Timeout after {timeout}s"
        except Exception as e:
            return False, str(e)

    def _probe_gpu_available(self) -> bool:
        """Cheap check: can nvidia-smi talk to the GPU at all?"""
        smi_path = self._find_nvidia_smi()
        if not smi_path:
            return False
        ok, out = self._run_command([smi_path, "-L"], timeout=10)
        return ok and ("GPU" in out)

    def _wait_for_gpu_recovery(self) -> bool:
        """Poll nvidia-smi a few times after a recovery action."""
        for i in range(max(1, self.recovery_verify_attempts)):
            if self._probe_gpu_available():
                return True
            time.sleep(max(0.5, self.recovery_verify_interval_seconds))
        return False

    def _restart_docker_containers(self) -> bool:
        """Attempt to restart configured docker containers."""
        containers = self.recovery_docker_containers or []
        if not containers:
            self.logger.warning(
                "Recovery: restart_docker_containers skipped (recovery_docker_containers is empty)"
            )
            return False

        ok_any = False
        for name in containers:
            self.logger.warning(f"Recovery: restarting docker container '{name}'")
            ok, out = self._run_command(["docker", "restart", name], timeout=120)
            if ok:
                ok_any = True
            else:
                self.logger.warning(f"Recovery: docker restart failed for '{name}': {out}")
        return ok_any

    def _restart_windows_service(self, service_name: str) -> bool:
        """Restart a Windows service using sc.exe."""
        if os.name != "nt":
            return False
        service_name = (service_name or "").strip()
        if not service_name:
            return False

        # Stop (ignore failure if already stopped)
        ok_stop, out_stop = self._run_command(["sc.exe", "stop", service_name], timeout=60)
        if not ok_stop:
            self.logger.warning(f"Recovery: sc stop {service_name} failed: {out_stop}")

        # Start
        ok_start, out_start = self._run_command(["sc.exe", "start", service_name], timeout=60)
        if not ok_start:
            self.logger.warning(f"Recovery: sc start {service_name} failed: {out_start}")
            return False
        return True

    def _restart_docker_service(self) -> bool:
        """
        Attempt to restart Docker Desktop service.

        This can reset WSL/Docker integration and restore GPU access for containers,
        but usually requires Administrator privileges.
        """
        services = self.recovery_docker_services or []
        if not services:
            self.logger.warning(
                "Recovery: restart_docker_service skipped (recovery_docker_services is empty)"
            )
            return False

        ok_any = False
        for svc in services:
            self.logger.warning(f"Recovery: restarting Windows service '{svc}'")
            if self._restart_windows_service(svc):
                ok_any = True
        return ok_any

    def _wsl_shutdown(self) -> bool:
        """Attempt to reset WSL (can help reset CUDA/WSL/Docker Desktop integration)."""
        if os.name != "nt":
            return False
        self.logger.warning("Recovery: running 'wsl.exe --shutdown'")
        ok, out = self._run_command(["wsl.exe", "--shutdown"], timeout=60)
        if not ok:
            self.logger.warning(f"Recovery: wsl --shutdown failed: {out}")
        return ok

    def _pnputil_restart_display_adapter(self) -> bool:
        """
        Attempt to restart the display adapter using pnputil.
        This may require Administrator privileges and may disrupt display output briefly.
        """
        if os.name != "nt":
            return False
        if not self.recovery_pnputil_restart_display:
            self.logger.warning(
                "Recovery: pnputil_restart_display skipped (recovery_pnputil_restart_display=false)"
            )
            return False

        instance_id = self.recovery_pnputil_instance_id
        if not instance_id:
            ok, out = self._run_command(["pnputil", "/enum-devices", "/class", "Display"], timeout=30)
            if not ok:
                self.logger.warning(f"Recovery: pnputil enum-devices failed: {out}")
                return False

            # Parse "Instance ID:" lines
            ids = []
            for line in out.splitlines():
                m = re.search(r"Instance ID:\s*(.+)$", line.strip(), re.IGNORECASE)
                if m:
                    ids.append(m.group(1).strip())
            if not ids:
                self.logger.warning("Recovery: could not find any Display Instance ID via pnputil")
                return False

            # Prefer NVIDIA if present
            preferred = None
            for _id in ids:
                if "PCI\\VEN_10DE" in _id.upper():
                    preferred = _id
                    break
            instance_id = preferred or ids[0]

        self.logger.warning(f"Recovery: pnputil restart-device '{instance_id}'")
        ok, out = self._run_command(["pnputil", "/restart-device", instance_id], timeout=60)
        if not ok:
            self.logger.warning(f"Recovery: pnputil restart-device failed: {out}")
        return ok

    def _attempt_gpu_lost_recovery(self) -> bool:
        """
        Try configured recovery steps before rebooting the machine.
        Returns True if GPU becomes available again, False otherwise.
        """
        if not self.recovery_enabled:
            return False

        self.logger.error(f"GPU is lost - attempting recovery steps: {self.recovery_steps}")

        for step in self.recovery_steps:
            step = (step or "").strip()
            if not step:
                continue

            try:
                if step == "restart_docker_containers":
                    self._restart_docker_containers()
                elif step == "restart_docker_service":
                    self._restart_docker_service()
                elif step == "restart_comfyui":
                    self._restart_comfyui()
                elif step == "wsl_shutdown":
                    self._wsl_shutdown()
                elif step == "pnputil_restart_display":
                    self._pnputil_restart_display_adapter()
                elif step == "reboot":
                    # Don't "recover" here; this is the terminal fallback
                    return False
                else:
                    self.logger.warning(f"Recovery: unknown step '{step}' (skipping)")
                    continue

                if self._wait_for_gpu_recovery():
                    self.logger.error("Recovery: GPU appears responsive again after step: " + step)
                    return True
                self.logger.warning(f"Recovery: GPU still not responsive after step: {step}")

            except Exception as e:
                self.logger.warning(f"Recovery: step '{step}' raised exception: {e}")

        return False

    def _set_power_limit(self, wattage: float):
        """Set GPU power limit using nvidia-smi"""
        if wattage is None:
            return

        # Skip if we've already been denied permissions (don't keep trying)
        if self._power_limit_permission_denied:
            return

        smi_path = self._find_nvidia_smi()
        if not smi_path:
            self.logger.warning("nvidia-smi not found - cannot set power limit")
            return

        try:
            # First, check current power limit and min/max limits
            cmd = [smi_path, "-q", "-d", "POWER"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

            if result.returncode != 0:
                self.logger.warning(f"Failed to query power limits: {result.stderr.strip()}")
                return

            # Parse min/max limits from output
            min_limit = None
            max_limit = None
            for line in result.stdout.split("\n"):
                if "Min Power Limit" in line:
                    min_limit = self._to_float(line.split(":")[1].strip())
                elif "Max Power Limit" in line:
                    max_limit = self._to_float(line.split(":")[1].strip())

            # Validate wattage is within limits
            if min_limit and wattage < min_limit:
                self.logger.warning(
                    f"Requested power limit {wattage}W is below minimum {min_limit}W. Using {min_limit}W instead."
                )
                wattage = min_limit
            elif max_limit and wattage > max_limit:
                self.logger.warning(
                    f"Requested power limit {wattage}W is above maximum {max_limit}W. Using {max_limit}W instead."
                )
                wattage = max_limit

            # Set the power limit
            cmd = [smi_path, "-pl", str(wattage)]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

            if result.returncode == 0:
                self.logger.info(f"GPU power limit set to {wattage}W")
                # Verify it was set correctly
                time.sleep(1)  # Brief wait for setting to take effect
                cmd = [smi_path, "-q", "-d", "POWER"]
                verify_result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                if verify_result.returncode == 0:
                    for line in verify_result.stdout.split("\n"):
                        if "Current Power Limit" in line:
                            current_limit = self._to_float(line.split(":")[1].strip())
                            if abs(current_limit - wattage) < 1.0:  # Allow 1W tolerance
                                self.logger.info(f"Power limit verified: {current_limit}W")
                            else:
                                self.logger.warning(
                                    f"Power limit set to {wattage}W but current is {current_limit}W"
                                )
            else:
                error_msg = result.stderr.strip() or result.stdout.strip()
                error_lower = error_msg.lower()

                # Check for permission issues
                if (
                    "insufficient" in error_lower
                    or "permission" in error_lower
                    or "access denied" in error_lower
                ):
                    self._power_limit_permission_denied = True
                    self.logger.warning(
                        f"Power limit setting requires administrator privileges: {error_msg}. "
                        f"Power limit will not be set. GPU will use default power limits. "
                        f"To enable power limit control, run this script as administrator."
                    )
                elif "not supported" in error_lower or "not available" in error_lower:
                    self.logger.warning(f"Power limit setting not supported on this GPU: {error_msg}")
                else:
                    self.logger.error(f"Failed to set power limit: {error_msg}")

        except subprocess.TimeoutExpired:
            self.logger.error("nvidia-smi timeout while setting power limit")
        except Exception as e:
            self.logger.error(f"Exception while setting power limit: {str(e)}")

    def _classify_snapshot_error(self, error_msg: str) -> str:
        """Classify snapshot errors into stable categories for safer escalation."""
        msg = (error_msg or "").lower()
        if "nvidia-smi not found" in msg:
            return "smi_not_found"
        if "timeout" in msg:
            return "timeout"
        if "no data returned" in msg:
            return "no_data"
        # Definite "GPU lost" patterns (NVML can't see the device)
        if (
            "gpu is lost" in msg
            or "no devices were found" in msg
            or "unable to determine the device handle" in msg
            or "gpu has fallen off the bus" in msg
        ):
            return "gpu_lost"
        # NVML/driver init failures can be transient or indicate driver crash
        if "failed to initialize nvml" in msg or "nvidia-smi has failed" in msg:
            return "nvml_init_failed"
        if "couldn't communicate with the nvidia driver" in msg:
            return "driver_comm_failed"
        if "driver/library version mismatch" in msg:
            return "driver_version_mismatch"
        return "unknown"

    def _get_gpu_snapshot(self) -> Dict:
        """Enhanced GPU snapshot with error detection"""
        smi_path = self._find_nvidia_smi()
        if not smi_path:
            error_msg = "nvidia-smi not found"
            return {
                "error": error_msg,
                "error_type": self._classify_snapshot_error(error_msg),
                "timestamp": datetime.now().isoformat(),
            }

        try:
            # Use basic fields that are more likely to work across nvidia-smi versions
            query_fields = [
                "name",
                "utilization.gpu",
                "utilization.memory",
                "memory.used",
                "memory.total",
                "power.draw",
                "power.limit",
                "temperature.gpu",
                "pstate",
                "clocks.gr",
                "clocks.mem",
            ]

            # Request CSV output; some driver versions include units. We'll robustly parse either way.
            cmd = [smi_path, "--query-gpu=" + ",".join(query_fields), "--format=csv"]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)

            if result.returncode != 0:
                error_msg = f"nvidia-smi failed: {result.stderr.strip()}"
                self.logger.warning(f"GPU snapshot failed: {error_msg}")
                return {
                    "error": error_msg,
                    "error_type": self._classify_snapshot_error(error_msg),
                    "timestamp": datetime.now().isoformat(),
                }

            # Parse CSV output (skip header line)
            lines = result.stdout.strip().split("\n")
            if len(lines) < 2:
                error_msg = "No data returned from nvidia-smi"
                return {
                    "error": error_msg,
                    "error_type": self._classify_snapshot_error(error_msg),
                    "timestamp": datetime.now().isoformat(),
                }

            # Skip header line and get data
            values = [v.strip() for v in lines[1].split(",")]
            while len(values) < len(query_fields):
                values.append("N/A")

            data = dict(zip(query_fields, values))
            data["timestamp"] = datetime.now().isoformat()

            # Normalize key numeric fields to unitless numbers for clean logging/analysis
            # Keep originals if parsing fails
            try:
                data["utilization.gpu"] = int(self._to_float(data.get("utilization.gpu")))
            except Exception:
                pass
            try:
                data["utilization.memory"] = int(self._to_float(data.get("utilization.memory")))
            except Exception:
                pass
            try:
                data["memory.used"] = int(self._to_float(data.get("memory.used")))
            except Exception:
                pass
            try:
                data["memory.total"] = int(self._to_float(data.get("memory.total")))
            except Exception:
                pass
            try:
                data["power.draw"] = round(self._to_float(data.get("power.draw")), 2)
            except Exception:
                pass
            try:
                data["power.limit"] = round(self._to_float(data.get("power.limit")), 2)
            except Exception:
                pass
            try:
                raw_temp = data.get("temperature.gpu")
                if raw_temp is not None and str(raw_temp).strip():
                    # nvidia-smi may return "72" or "72 C" or "N/A"
                    data["temperature.gpu"] = int(self._to_float(str(raw_temp).replace(" C", "").strip()))
                else:
                    data["temperature.gpu"] = None
            except Exception:
                data["temperature.gpu"] = None

            # Calculate derived metrics
            try:
                mem_used = self._to_float(str(data.get("memory.used", "nan")))
                mem_total = self._to_float(str(data.get("memory.total", "nan")))
                data["memory.percent"] = (
                    round((mem_used / mem_total) * 100, 2) if mem_total and mem_total == mem_total else None
                )
            except Exception:
                data["memory.percent"] = None

            try:
                power_draw = self._to_float(str(data.get("power.draw", "nan")))
                power_limit = self._to_float(str(data.get("power.limit", "nan")))
                data["power.percent"] = (
                    round((power_draw / power_limit) * 100, 2)
                    if power_limit and power_limit == power_limit
                    else None
                )
            except Exception:
                data["power.percent"] = None

            # Detect potential issues
            data["warnings"] = self._detect_gpu_warnings(data)

            return data

        except subprocess.TimeoutExpired:
            self.logger.error("nvidia-smi timeout - GPU may be unresponsive")
            error_msg = "nvidia-smi timeout"
            return {
                "error": error_msg,
                "error_type": self._classify_snapshot_error(error_msg),
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as e:
            self.logger.error(f"GPU snapshot exception: {str(e)}")
            error_msg = f"snapshot exception: {str(e)}"
            return {
                "error": error_msg,
                "error_type": self._classify_snapshot_error(error_msg),
                "timestamp": datetime.now().isoformat(),
            }

    def _detect_gpu_warnings(self, gpu_data: Dict) -> List[str]:
        """Detect potential GPU issues from current metrics"""
        warnings = []

        try:
            power_percent = gpu_data.get("power.percent")
            if power_percent and power_percent > 95:
                warnings.append(f"HIGH_POWER:{power_percent}%")

            # Check for high memory usage
            memory_percent = gpu_data.get("memory.percent")
            if memory_percent and memory_percent > 90:
                warnings.append(f"HIGH_MEMORY:{memory_percent}%")

            # Check for high GPU temperature
            temp = gpu_data.get("temperature.gpu")
            if temp is not None and isinstance(temp, (int, float)):
                if temp >= getattr(self, "temperature_critical_threshold", 85):
                    warnings.append(f"CRITICAL_TEMP:{temp}°C")
                elif temp >= getattr(self, "temperature_warning_threshold", 80):
                    warnings.append(f"HIGH_TEMP:{temp}°C")

        except Exception as e:
            warnings.append(f"WARNING_DETECTION_ERROR:{str(e)}")

        return warnings

    def _monitor_windows_event_log(self):
        """Monitor Windows Event Log for GPU-related errors"""
        if not WINDOWS_EVENT_LOG_AVAILABLE:
            self.logger.debug("Windows Event Log monitoring not available (pywin32 not installed)")
            return

        try:
            # Check for NVIDIA driver errors
            hand = win32evtlog.OpenEventLog(None, "System")
            flags = win32evtlog.EVENTLOG_BACKWARDS_READ | win32evtlog.EVENTLOG_SEQUENTIAL_READ

            events = win32evtlog.ReadEventLog(hand, flags, 0)

            for event in events:
                if event.EventID in [14, 15, 16, 17]:  # Common GPU error IDs
                    event_time = event.TimeGenerated.Format()
                    event_msg = win32evtlogutil.SafeFormatMessage(event, "System")

                    if any(
                        keyword in event_msg.lower()
                        for keyword in ["nvidia", "gpu", "display", "graphics", "cuda"]
                    ):
                        self.failure_logger.warning(f"GPU Event Log Error: {event_time} - {event_msg}")

        except Exception as e:
            self.logger.debug(f"Event log monitoring error: {str(e)}")

    def _monitor_comfyui_logs(self):
        """Monitor ComfyUI logs for GPU-related errors"""
        comfyui_log_paths = [
            "ComfyUI/comfyui_error.txt",
            "ComfyUI/comfyui_output.txt",
            "ComfyUI/startup_log.txt",
            "ComfyUI/logs/watcher.log",
        ]

        for log_path in comfyui_log_paths:
            if os.path.exists(log_path):
                try:
                    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()

                    # Look for GPU-related errors
                    # Exclude configuration errors (PyTorch not compiled with CUDA) - these are setup issues, not GPU crashes
                    gpu_error_patterns = [
                        r"CUDA.*error(?!.*not compiled)",
                        r"GPU.*memory.*error",
                        r"cuda.*out.*of.*memory",
                        r"AssertionError.*CUDA(?!.*not compiled)",
                        r"nvidia.*driver.*error",
                        r"GPU.*crash",
                        r"device.*not.*found",
                    ]

                    # Filter out "Torch not compiled with CUDA" errors - these are configuration issues, not GPU failures
                    ignore_patterns = [
                        r"Torch not compiled with CUDA",
                        r"AssertionError.*Torch not compiled with CUDA",
                    ]

                    # Check if content contains ignorable patterns first
                    should_ignore = False
                    for ignore_pattern in ignore_patterns:
                        if re.search(ignore_pattern, content, re.IGNORECASE):
                            should_ignore = True
                            self.logger.debug(
                                f"Ignoring configuration error in {log_path}: {ignore_pattern}"
                            )
                            break

                    if not should_ignore:
                        for pattern in gpu_error_patterns:
                            matches = re.findall(pattern, content, re.IGNORECASE)
                            if matches:
                                self.failure_logger.warning(f"ComfyUI GPU Error in {log_path}: {matches}")

                except Exception as e:
                    self.logger.debug(f"Error reading ComfyUI log {log_path}: {str(e)}")

    def _detect_gpu_crash(self, current_snapshot: Dict) -> bool:
        """Detect if GPU has crashed or become unresponsive (report-only mode)"""
        if "error" in current_snapshot:
            error_msg = current_snapshot.get("error", "") or ""
            error_type = current_snapshot.get("error_type") or self._classify_snapshot_error(error_msg)

            # Don't escalate on missing tooling
            if error_type == "smi_not_found":
                self.logger.warning("GPU snapshot error: nvidia-smi not found (not counting as failure)")
                return False

            # Treat these as transient by default (avoid false-positive restarts/reboots)
            transient_types = {"timeout", "no_data", "unknown"}
            if (not self.count_transient_failures) and (error_type in transient_types):
                self.logger.warning(
                    f"Transient GPU snapshot error (not counting as failure): {error_type} | {error_msg}"
                )
                return False

            self.gpu_unresponsive_count += 1

            if self.gpu_unresponsive_count >= self.max_unresponsive_threshold:
                mode_msg = "REPORT-ONLY MODE: " if self.report_only else ""
                self.failure_logger.error(
                    f"{mode_msg}GPU CRASH DETECTED: {self.gpu_unresponsive_count} consecutive failures"
                )
                self._log_failure_event(
                    "GPU_CRASH",
                    {
                        "consecutive_failures": self.gpu_unresponsive_count,
                        "last_error": current_snapshot.get("error"),
                        "timestamp": current_snapshot.get("timestamp"),
                        "report_only": self.report_only,
                    },
                )
                # In report-only mode, we only log - no restart actions are taken
                if self.report_only:
                    self.logger.info("REPORT-ONLY MODE: GPU crash detected but no restart will be triggered")
                else:
                    # Check if GPU is lost (requires system reboot)
                    # Prefer structured error_type when present, but keep string fallback.
                    if error_type == "gpu_lost" or ("GPU is lost" in error_msg) or ("No devices were found" in error_msg):
                        # GPU lost: try recovery ladder first; reboot only as last resort.
                        recovered = self._attempt_gpu_lost_recovery()
                        if recovered:
                            # Reset counter so we don't immediately re-trigger.
                            self.gpu_unresponsive_count = 0
                        else:
                            if self.recovery_fallback_to_reboot:
                                self.logger.error("Recovery failed - falling back to system reboot...")
                                self._reboot_system()
                            else:
                                self.logger.error(
                                    "Recovery failed and recovery_fallback_to_reboot=false; not rebooting."
                                )
                    else:
                        # GPU crash but not lost - restart ComfyUI
                        self.logger.error("GPU crash detected - initiating ComfyUI restart...")
                        self._restart_comfyui()
                return True
        else:
            self.gpu_unresponsive_count = 0

        return False

    def _log_failure_event(self, failure_type: str, details: Dict):
        """Log a GPU failure event"""
        failure_event = {
            "type": failure_type,
            "timestamp": datetime.now().isoformat(),
            "details": details,
            "gpu_history": self.gpu_history[-10:] if self.gpu_history else [],  # Last 10 snapshots
        }

        self.failure_events.append(failure_event)

        # Save to JSON file
        try:
            with open(self.failure_log, "w", encoding="utf-8") as f:
                json.dump(self.failure_events, f, indent=2)
        except Exception as e:
            self.logger.error(f"Failed to save failure log: {str(e)}")

    def _restart_comfyui(self):
        """Restart ComfyUI server when GPU crash is detected"""
        try:
            self.logger.info("Attempting to restart ComfyUI...")

            # Find ComfyUI Python processes
            comfyui_processes = []
            for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                try:
                    if proc.info["name"] and "python" in proc.info["name"].lower():
                        cmdline = proc.info.get("cmdline", [])
                        if cmdline:
                            # Check if this is a ComfyUI process (has main.py in command line)
                            cmdline_str = " ".join(str(arg) for arg in cmdline)
                            if "main.py" in cmdline_str and (
                                "ComfyUI" in cmdline_str or "comfyui" in cmdline_str.lower()
                            ):
                                comfyui_processes.append(proc)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            if not comfyui_processes:
                self.logger.warning("No ComfyUI processes found to restart")
                return

            # Stop ComfyUI processes
            for proc in comfyui_processes:
                try:
                    self.logger.info(f"Stopping ComfyUI process PID {proc.pid}")
                    proc.terminate()
                    proc.wait(timeout=10)
                except psutil.TimeoutExpired:
                    self.logger.warning(f"Process {proc.pid} did not terminate, forcing kill")
                    proc.kill()
                except Exception as e:
                    self.logger.error(f"Error stopping process {proc.pid}: {str(e)}")

            # Wait a bit before restarting
            self.logger.info("Waiting 5 seconds before restarting ComfyUI...")
            time.sleep(5)

            # Find the ComfyUI directory and main.py
            script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            comfyui_dir = os.path.join(script_dir, "ComfyUI")
            main_py = os.path.join(comfyui_dir, "main.py")

            if not os.path.exists(main_py):
                self.logger.error(f"ComfyUI main.py not found at {main_py}")
                return

            # Find Python executable
            embedded_python = os.path.join(script_dir, "python_embeded", "python.exe")
            if os.path.exists(embedded_python):
                python_exe = embedded_python
            else:
                python_exe = sys.executable

            # Restart ComfyUI
            self.logger.info(f"Starting ComfyUI with {python_exe}")
            os.chdir(comfyui_dir)

            # Start ComfyUI in a new process (detached)
            if os.name == "nt":  # Windows
                subprocess.Popen(
                    [python_exe, "main.py", "--listen", "0.0.0.0", "--port", "8188"],
                    cwd=comfyui_dir,
                    creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.DETACHED_PROCESS,
                )
            else:
                subprocess.Popen(
                    [python_exe, "main.py", "--listen", "0.0.0.0", "--port", "8188"],
                    cwd=comfyui_dir,
                    start_new_session=True,
                )

            self.logger.info("ComfyUI restart initiated")

        except Exception as e:
            self.logger.error(f"Failed to restart ComfyUI: {str(e)}")
            self.failure_logger.error(f"RESTART FAILED: {str(e)}")

    def _reboot_system(self):
        """Reboot the system when GPU is lost (only if not in report-only mode)"""
        # In report-only mode, we don't reboot - just log
        if self.report_only:
            self.logger.info(
                "REPORT-ONLY MODE: GPU is lost but no reboot will be triggered (gpu_crash_monitor.ps1 handles reboots)"
            )
            return

        try:
            self.logger.error("GPU is lost - system reboot required to recover GPU")
            self.failure_logger.error("SYSTEM REBOOT INITIATED: GPU is lost")

            # Log the reboot action
            self._log_failure_event(
                "SYSTEM_REBOOT",
                {
                    "reason": "GPU is lost",
                    "timestamp": datetime.now().isoformat(),
                    "report_only": self.report_only,
                },
            )

            # Wait a moment to ensure logs are written
            time.sleep(2)

            # Reboot the system
            if os.name == "nt":  # Windows
                self.logger.error("Executing: shutdown /r /t 30 /c 'GPU is lost - system reboot required'")
                subprocess.run(
                    ["shutdown", "/r", "/t", "30", "/c", "GPU is lost - system reboot required"], check=False
                )
            else:  # Linux/Unix
                self.logger.error("Executing: reboot")
                subprocess.run(["reboot"], check=False)

        except Exception as e:
            self.logger.error(f"Failed to reboot system: {str(e)}")
            self.failure_logger.error(f"REBOOT FAILED: {str(e)}")

    def _detect_generation(self, snapshot: Dict) -> bool:
        """Detect if generation is currently occurring based on GPU metrics"""
        if "error" in snapshot:
            return False

        # Check GPU utilization - if above threshold, likely generating
        utilization = snapshot.get("utilization.gpu")
        if utilization is not None:
            try:
                util_value = int(self._to_float(str(utilization)))
                if util_value >= self.generation_utilization_threshold:
                    return True
            except (ValueError, TypeError):
                pass

        # Also check memory utilization as secondary indicator
        memory_percent = snapshot.get("memory.percent")
        if memory_percent is not None and memory_percent > 50:
            if utilization is not None:
                try:
                    util_value = int(self._to_float(str(utilization)))
                    if util_value > 5:
                        return True
                except (ValueError, TypeError):
                    pass

        return False

    def _correlate_performance_with_failures(self):
        """Analyze GPU performance patterns before failures"""
        if len(self.gpu_history) < 5:
            return

        recent_history = self.gpu_history[-5:]

        # Check for temperature spikes
        temps = [
            self._to_float(h.get("temperature.gpu", "nan"))
            for h in recent_history
            if "temperature.gpu" in h
        ]
        if len(temps) >= 3:
            temp_trend = temps[-1] - temps[0]
            if temp_trend > 10:
                self.failure_logger.warning(f"TEMPERATURE SPIKE DETECTED: {temp_trend}°C increase")

        # Check for power consumption spikes
        powers = [self._to_float(h.get("power.draw", "nan")) for h in recent_history if "power.draw" in h]
        if len(powers) >= 3:
            power_trend = powers[-1] - powers[0]
            if power_trend > 50:
                self.failure_logger.warning(f"POWER SPIKE DETECTED: {power_trend}W increase")

    def _monitoring_loop(self):
        """Main monitoring loop"""
        mode_msg = " (REPORT-ONLY MODE)" if self.report_only else ""
        gen_log_msg = (
            " (Generation logging: ENABLED)" if self.enable_generation_logging else " (Generation logging: DISABLED)"
        )
        # Print source path so we can verify which copy is running (portable vs runpod).
        try:
            self.logger.info(f"Monitor source: {os.path.abspath(__file__)}")
            self.logger.info(f"Monitor argv0: {sys.argv[0]}")
        except Exception:
            pass
        self.logger.info(f"Starting enhanced GPU monitoring{mode_msg}{gen_log_msg}")

        while not self._stop_event.is_set():
            try:
                # Get GPU snapshot
                snapshot = self._get_gpu_snapshot()
                self.gpu_history.append(snapshot)

                # Keep only last 100 snapshots
                if len(self.gpu_history) > 100:
                    self.gpu_history = self.gpu_history[-100:]

                # Log snapshot
                if "error" not in snapshot:
                    warnings = snapshot.get("warnings", [])
                    warnings_str = f" | Warnings: {warnings}" if warnings else ""
                    temp = snapshot.get("temperature.gpu")
                    temp_str = f"{temp}°C" if temp is not None else "N/A"
                    self.logger.info(
                        f"GPU: {snapshot.get('name')} | "
                        f"Util: {snapshot.get('utilization.gpu')}% | "
                        f"Mem: {snapshot.get('memory.used')}/{snapshot.get('memory.total')} MB "
                        f"({snapshot.get('memory.percent')}%) | "
                        f"Power: {snapshot.get('power.draw')}/{snapshot.get('power.limit')} W "
                        f"({snapshot.get('power.percent')}%) | "
                        f"Temp: {temp_str}{warnings_str}"
                    )
                else:
                    self.logger.warning(f"GPU Error: {snapshot.get('error')}")

                # Detect if generation is occurring (only if generation logging is enabled)
                if self.enable_generation_logging:
                    was_generating = self.is_generating
                    self.is_generating = self._detect_generation(snapshot)

                    if self.is_generating and not was_generating:
                        self.logger.info(
                            f"=== GENERATION DETECTED - Switching to {self.generation_sampling_interval}s logging interval ==="
                        )
                    elif not self.is_generating and was_generating:
                        self.logger.info(
                            f"=== GENERATION COMPLETE - Switching to {self.sampling_interval}s logging interval ==="
                        )
                else:
                    self.is_generating = False

                # Detect GPU crash
                self._detect_gpu_crash(snapshot)

                # Periodically verify and re-apply power limit if configured
                if self.power_limit is not None and not self._power_limit_permission_denied:
                    self._power_limit_check_counter += 1
                    if self._power_limit_check_counter >= 10:
                        self._power_limit_check_counter = 0
                        if "error" not in snapshot:
                            current_limit = self._to_float(str(snapshot.get("power.limit", "nan")))
                            if not (abs(current_limit - self.power_limit) < 1.0):
                                self.logger.warning(
                                    f"Power limit drifted to {current_limit}W (expected {self.power_limit}W), re-applying..."
                                )
                                self._set_power_limit(self.power_limit)

                # Correlate performance patterns
                self._correlate_performance_with_failures()

                # Monitor system logs periodically (less frequent during generation)
                check_interval = 5 if (self.enable_generation_logging and self.is_generating) else 10
                if len(self.gpu_history) % check_interval == 0:
                    self._monitor_windows_event_log()
                    self._monitor_comfyui_logs()

                # Save CSV data
                self._save_csv_data(snapshot)

            except Exception as e:
                self.logger.error(f"Monitoring loop error: {str(e)}")

            # Dynamic interval: 1 second during generation (if enabled), normal interval otherwise
            current_interval = (
                self.generation_sampling_interval if (self.enable_generation_logging and self.is_generating) else self.sampling_interval
            )
            if self._stop_event.wait(current_interval):
                break

        self.logger.info("Enhanced GPU monitoring stopped")

    def _save_csv_data(self, snapshot: Dict):
        """Save GPU data to CSV for analysis"""
        try:
            header = (
                "timestamp,name,utilization_gpu,utilization_memory,"
                "memory_used,memory_total,memory_percent,power_draw,power_limit,"
                "power_percent,temperature_gpu,pstate,clocks_gr,clocks_mem,warnings\n"
            )

            is_new = not os.path.exists(self.csv_file)
            with open(self.csv_file, "a", encoding="utf-8") as f:
                if is_new:
                    f.write(header)

                warnings_str = ";".join(snapshot.get("warnings", [])) if snapshot.get("warnings") else ""
                temp = snapshot.get("temperature.gpu")
                temp_val = temp if temp is not None else ""
                f.write(
                    f"{snapshot.get('timestamp')},{snapshot.get('name')},"
                    f"{snapshot.get('utilization.gpu')},"
                    f"{snapshot.get('utilization.memory')},{snapshot.get('memory.used')},"
                    f"{snapshot.get('memory.total')},{snapshot.get('memory.percent')},"
                    f"{snapshot.get('power.draw')},{snapshot.get('power.limit')},"
                    f"{snapshot.get('power.percent')},{temp_val},{snapshot.get('pstate')},"
                    f"{snapshot.get('clocks.gr')},{snapshot.get('clocks.mem')},"
                    f"{warnings_str}\n"
                )
        except Exception as e:
            self.logger.error(f"Failed to save CSV data: {str(e)}")

    def start_monitoring(self):
        """Start the enhanced GPU monitoring"""
        if self._monitor_thread and self._monitor_thread.is_alive():
            self.logger.warning("Monitoring already running")
            return

        self._stop_event.clear()
        self._monitor_thread = threading.Thread(target=self._monitoring_loop, daemon=True)
        self._monitor_thread.start()
        self.logger.info("Enhanced GPU monitoring started")

    def stop_monitoring(self):
        """Stop the enhanced GPU monitoring"""
        self._stop_event.set()
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=5.0)
        self.logger.info("Enhanced GPU monitoring stopped")

    def _cleanup(self):
        """Cleanup on exit"""
        self.stop_monitoring()

    def get_failure_summary(self) -> Dict:
        """Get summary of detected failures"""
        return {
            "total_failures": len(self.failure_events),
            "failure_types": list(set(f["type"] for f in self.failure_events)),
            "recent_failures": self.failure_events[-5:] if self.failure_events else [],
            "gpu_unresponsive_count": self.gpu_unresponsive_count,
        }


def main():
    """Main function for standalone GPU monitoring"""
    parser = argparse.ArgumentParser(
        description="Enhanced GPU Monitor - Monitors GPU status and detects failures",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Configuration:
  Config file is read from (in order):
    1. C:/Logs/gpu_monitor_config.json
    2. scripts/automation/gpu_monitor_config.json
    3. Default values if no config found

  Command-line arguments override config file values.

  Example config file (C:/Logs/gpu_monitor_config.json):
    {
      "report_only": true,
      "log_file": "C:/Logs/gpu_crash.log",
      "failure_threshold": 3
    }
        """,
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        default=None,
        help="Enable report-only mode (no restarts). Overrides config file.",
    )
    parser.add_argument(
        "--enable-restarts",
        action="store_true",
        default=None,
        help="Enable restart actions (disable report-only). Overrides config file.",
    )
    parser.add_argument(
        "--config", type=str, default=None, help="Path to config file (default: C:/Logs/gpu_monitor_config.json)"
    )
    parser.add_argument("--log-file", type=str, default=None, help="Path to log file (overrides config)")

    args = parser.parse_args()

    # Determine report_only from command-line (overrides config)
    report_only = None
    if args.report_only:
        report_only = True
    elif args.enable_restarts:
        report_only = False

    print("Enhanced GPU Monitor")
    print("====================")

    # Initialize with config and command-line overrides
    monitor = EnhancedGPUMonitor(log_file=args.log_file, report_only=report_only, config_file=args.config)

    mode_msg = " (REPORT-ONLY MODE)" if monitor.report_only else " (RESTART ENABLED)"
    print(f"Mode: {mode_msg}")
    print(f"Config: report_only={monitor.report_only}")
    print("")

    try:
        monitor.start_monitoring()
        print("GPU monitoring started. Press Ctrl+C to stop.")

        # Keep running until interrupted
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nStopping GPU monitoring...")
        monitor.stop_monitoring()

        # Print failure summary
        summary = monitor.get_failure_summary()
        print("\nFailure Summary:")
        print(f"Total failures detected: {summary['total_failures']}")
        print(f"Failure types: {summary['failure_types']}")

    except Exception as e:
        print(f"Error: {str(e)}")
        monitor.stop_monitoring()


if __name__ == "__main__":
    main()

