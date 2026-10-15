"""
Pytest configuration and fixtures.
"""
import os
import sys
import tempfile
import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set test environment
os.environ['APP_ENV'] = 'test'
os.environ['DATABASE_URL'] = 'sqlite:///test_autopilot.db'
os.environ['TIMEZONE'] = 'America/New_York'
os.environ['EMERGENCY_STOP'] = 'false'
os.environ['COST_KILL_SWITCH'] = 'false'
os.environ['ENCRYPTION_KEY'] = 'dGVzdF9lbmNyeXB0aW9uX2tleV8xMjM0NTY3ODkw'


@pytest.fixture(autouse=True)
def setup_test_env():
    """Set up test environment before each test."""
    os.environ['APP_ENV'] = 'test'
    os.environ['DATABASE_URL'] = 'sqlite:///test_autopilot.db'
    yield
    # Cleanup test database
    if os.path.exists('test_autopilot.db'):
        try:
            os.remove('test_autopilot.db')
        except OSError:
            pass


@pytest.fixture
def temp_dir():
    """Provide a temporary directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def sample_script():
    """Provide a sample video script."""
    return {
        'title': 'Why the Sky is Blue',
        'hook': 'Have you ever looked up and wondered why the sky is blue?',
        'script_sections': [
            {
                'heading': 'The Science',
                'text': 'Sunlight contains all colors of the rainbow. When it enters our atmosphere, '
                       'it collides with gas molecules. Blue light has a shorter wavelength, so it '
                       'scatters more than other colors. This is called Rayleigh scattering.'
            },
            {
                'heading': 'Why Not Violet?',
                'text': 'Violet light actually scatters even more than blue, but our eyes are more '
                       'sensitive to blue light. Plus, some violet light gets absorbed in the upper atmosphere.'
            }
        ],
        'cta': 'If you learned something new, hit that like button and subscribe for more science facts!',
        'description': 'Ever wondered why the sky is blue? The answer involves physics, light, and our eyes.',
        'tags': ['science', 'sky', 'blue sky', 'rayleigh scattering', 'physics', 'education'],
        'estimated_duration': 45,
        'video_type': 'short'
    }
