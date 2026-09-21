from scripts.windows_session_handoff import boot_owner_should_yield,WTS_ACTIVE,WTS_CONNECTED

def test_boot_owner_keeps_authority_without_interactive_user():
    assert boot_owner_should_yield([]) is False

def test_boot_owner_yields_to_active_authenticated_user():
    assert boot_owner_should_yield([{"username":"jerem","state":WTS_ACTIVE}]) is True

def test_boot_owner_yields_to_connected_authenticated_user():
    assert boot_owner_should_yield([{"username":"jerem","state":WTS_CONNECTED}]) is True

def test_service_session_without_username_does_not_force_handoff():
    assert boot_owner_should_yield([{"username":"","state":WTS_ACTIVE}]) is False
