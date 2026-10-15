"""
Tests for the Arabic Dashboard application and its routes.
اختبارات شاملة للوحة التحكم العربية وجميع مساراتها وقوالبها.
"""

import os
import pytest
from src.dashboard.app import create_app
from src.dashboard.db import init_db


@pytest.fixture(scope="module")
def app():
    """Create test application fixture."""
    os.environ["APP_ENV"] = "test"
    os.environ["SECRET_KEY"] = "test-secret-key-12345"
    os.environ["ADMIN_USERNAME"] = "admin"
    os.environ["ADMIN_PASSWORD"] = "admin123"
    init_db()
    
    application = create_app()
    application.config.update({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
    })
    return application


@pytest.fixture
def client(app):
    """Test client."""
    init_db()
    return app.test_client()


@pytest.fixture
def authenticated_client(app, client):
    """Authenticated client fixture."""
    with client:
        response = client.post(
            "/login",
            data={"username": "admin", "password": "admin123"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        yield client


def test_login_page_renders(client):
    """Test login page GET."""
    response = client.get("/login")
    assert response.status_code == 200
    assert "تسجيل الدخول".encode("utf-8") in response.data


def test_login_invalid_credentials(client):
    """Test login with bad credentials."""
    response = client.post(
        "/login",
        data={"username": "admin", "password": "wrongpassword"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "غير صحيحة".encode("utf-8") in response.data


def test_overview_page_authenticated(authenticated_client):
    """Test overview dashboard page."""
    response = authenticated_client.get("/")
    assert response.status_code == 200
    assert "نظرة عامة".encode("utf-8") in response.data


def test_calendar_page(authenticated_client):
    """Test publishing calendar page."""
    response = authenticated_client.get("/calendar")
    assert response.status_code == 200
    assert "تقويم النشر".encode("utf-8") in response.data


def test_videos_page(authenticated_client):
    """Test video library page."""
    response = authenticated_client.get("/videos")
    assert response.status_code == 200
    assert "مكتبة الفيديو".encode("utf-8") in response.data


def test_content_page(authenticated_client):
    """Test AI content manager page."""
    response = authenticated_client.get("/content")
    assert response.status_code == 200
    assert "مدير المحتوى".encode("utf-8") in response.data


def test_pipeline_page(authenticated_client):
    """Test production pipeline page."""
    response = authenticated_client.get("/pipeline")
    assert response.status_code == 200
    assert "خط إنتاج الفيديو".encode("utf-8") in response.data


def test_analytics_page(authenticated_client):
    """Test YouTube analytics page."""
    response = authenticated_client.get("/analytics")
    assert response.status_code == 200
    assert "تحليلات يوتيوب".encode("utf-8") in response.data


def test_revenue_page(authenticated_client):
    """Test revenue reports page."""
    response = authenticated_client.get("/revenue")
    assert response.status_code == 200
    assert "تقارير الإيرادات".encode("utf-8") in response.data


def test_experiments_page(authenticated_client):
    """Test experiments & strategy page."""
    response = authenticated_client.get("/experiments")
    assert response.status_code == 200
    assert "التجارب والاستراتيجية".encode("utf-8") in response.data


def test_errors_page(authenticated_client):
    """Test error log page."""
    response = authenticated_client.get("/errors")
    assert response.status_code == 200
    assert "الأخطاء والمشاكل".encode("utf-8") in response.data


def test_resources_page(authenticated_client):
    """Test cloud resource usage page."""
    response = authenticated_client.get("/resources")
    assert response.status_code == 200
    assert "موارد السحابة".encode("utf-8") in response.data


def test_quotas_page(authenticated_client):
    """Test API quota tracking page."""
    response = authenticated_client.get("/quotas")
    assert response.status_code == 200
    assert "حصص API".encode("utf-8") in response.data


def test_settings_page(authenticated_client):
    """Test settings page."""
    response = authenticated_client.get("/settings")
    assert response.status_code == 200
    assert "الإعدادات".encode("utf-8") in response.data


def test_security_page(authenticated_client):
    """Test security & account connection page."""
    response = authenticated_client.get("/security")
    assert response.status_code == 200
    assert "الأمان والحسابات".encode("utf-8") in response.data


def test_api_status_endpoint(authenticated_client):
    """Test API status JSON endpoint."""
    response = authenticated_client.get("/api/status")
    assert response.status_code == 200
    data = response.get_json()
    assert "status" in data
    assert "is_paused" in data


def test_api_pause_and_resume(authenticated_client):
    """Test pause and resume API endpoints."""
    # Test pause
    pause_resp = authenticated_client.post("/api/pause")
    assert pause_resp.status_code == 200
    assert pause_resp.get_json().get("status") == "success"
    assert pause_resp.get_json().get("is_paused") is True

    # Test resume
    resume_resp = authenticated_client.post("/api/resume")
    assert resume_resp.status_code == 200
    assert resume_resp.get_json().get("status") == "success"
    assert resume_resp.get_json().get("is_paused") is False
