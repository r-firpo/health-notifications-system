from django.urls import path, include
from users import urls as user_urls

urlpatterns = [
    path('api/', include(user_urls)),
]

# And the users/urls.py:

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from users.views import UserViewSet

router = DefaultRouter()
router.register(r'users', UserViewSet)

urlpatterns = [
    path('', include(router.urls)),
]