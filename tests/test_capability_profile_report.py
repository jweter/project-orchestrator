import pytest
from src.project_orchestrator.capability_profile_report import summarize_capability_profile

def test_empty_profile():
    profile = {}
    expected = {
        'model': None,
        'max_passing_level': 0,
        'levels': []
    }
    assert summarize_capability_profile(profile) == expected

def test_profile_with_two_levels():
    profile = {
        'model': 'ModelA',
        'max_passing_level': 2,
        'levels': {
            '1': {'pass_rate': 0.95, 'median_wall_s': 1.2, 'status': 'Pass'},
            '2': {'pass_rate': 0.90, 'median_wall_s': 1.5, 'status': 'Fail'}
        }
    }
    expected = {
        'model': 'ModelA',
        'max_passing_level': 2,
        'levels': [
            {'level': 1, 'pass_rate': 0.95, 'median_wall_s': 1.2, 'status': 'Pass'},
            {'level': 2, 'pass_rate': 0.90, 'median_wall_s': 1.5, 'status': 'Fail'}
        ]
    }
    assert summarize_capability_profile(profile) == expected
