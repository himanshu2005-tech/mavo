import pytest
from pydantic import ValidationError

from mavo.resources.device_config import DeviceMonitor, legacy_monitor


@pytest.mark.parametrize(
    "drive", ["\\\\server\\share", "C:\\Users", "https://example.com", "../../", "C:secret"]
)
def test_only_drive_counters_not_arbitrary_paths(drive):
    with pytest.raises(ValidationError):
        DeviceMonitor(drive=drive)


def test_legacy_c_drive_reports_and_default_quiet_alerts():
    monitor = legacy_monitor("Read free space on the C: drive. Send a notification on every check.")
    assert (
        monitor.kind == "storage"
        and monitor.drive == "C:"
        and monitor.notifications == "each_check"
    )
    assert legacy_monitor("Watch CPU and memory").notifications == "alerts"
