from django.contrib import admin
from django.urls import path

from example_proj.views import test_view


urlpatterns = [
    path("admin/", admin.site.urls),
    path("", test_view, name="test"),
]
