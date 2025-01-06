from django.http import Http404
from rest_framework import viewsets
from rest_framework.pagination import PageNumberPagination
from .models import User
from .serializers import UserSerializer

from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.shortcuts import get_object_or_404
import logging
from rest_framework.throttling import UserRateThrottle


logger = logging.getLogger(__name__)


class StandardResultsSetPagination(PageNumberPagination):
    page_size = 100
    page_size_query_param = 'page_size'
    max_page_size = 1000


class UserThrottle(UserRateThrottle):
    rate = '1000/day'

class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all().select_related('notification_preferences')
    serializer_class = UserSerializer
    pagination_class = StandardResultsSetPagination
    throttle_classes = [UserThrottle]

    def create(self, request, *args, **kwargs):
        try:
            return super().create(request, *args, **kwargs)
        except IntegrityError as e:
            logger.error(f"Database integrity error creating user: {str(e)}")
            return Response(
                {"error": "User creation failed. Email may already exist."},
                status=status.HTTP_400_BAD_REQUEST
            )
        except ValidationError as e:
            logger.error(f"Validation error creating user: {str(e)}")
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            logger.error(f"Unexpected error creating user: {str(e)}")
            return Response(
                {"error": "An unexpected error occurred"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def update(self, request, *args, **kwargs):
        try:
            return super().update(request, *args, **kwargs)
        except User.DoesNotExist:
            return Response(
                {"error": "User not found"},
                status=status.HTTP_404_NOT_FOUND
            )
        except ValidationError as e:
            logger.error(f"Validation error updating user {kwargs.get('pk')}: {str(e)}")
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            logger.error(f"Unexpected error updating user {kwargs.get('pk')}: {str(e)}")
            return Response(
                {"error": "An unexpected error occurred"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def destroy(self, request, *args, **kwargs):
        try:
            return super().destroy(request, *args, **kwargs)

        except Http404:
            return Response(
                {"error": "User not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        except Exception as e:
            logger.error(f"Error deleting user {kwargs.get('pk')}: {str(e)}")
            return Response(
                {"error": "An unexpected error occurred"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def get_object(self):
        try:
            return super().get_object()
        except User.DoesNotExist:
            raise Response(
                {"error": "User not found"},
                status=status.HTTP_404_NOT_FOUND
            )