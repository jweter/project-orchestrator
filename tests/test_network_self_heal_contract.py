from pathlib import Path
def test_network_guardian_contract():
 r=Path(__file__).parents[1];c=(r/"scripts/network_self_heal.py").read_text();b=(r/"scripts/windows_hands_off_boot.bat").read_text()
 assert "RECOVERY_SUPPRESSED_HUMAN_MODE" in c and "network-incidents" in c
 assert all(x in c for x in ("wlan_reconnect","wlan_service_restart","adapter_restart","RECOVERY_EXHAUSTED"))
 assert "allow_reboot and not human_mode" in c and "network_self_heal.py" in b
