#!/usr/bin/env python3
# -*- coding: utf-8 -*-
__author__ = 'g3n35i5'

from shopdb.models import *
from shopdb.api import app
from tests.base_api import BaseAPITestCase


class GetImageAPITestCase(BaseAPITestCase):
    def test_get_existing_image(self):
        """This test ensures that an existing image is returned."""
        res = self.get('images/valid_image.png')
        filepath = app.config['UPLOAD_FOLDER'] + 'valid_image.png'
        with open(filepath, 'rb') as image:
            bytes = image.read()
        self.assertEqual(res.data, bytes)

    def test_get_non_existing_image(self):
        """
        This test ensures that an exception is made when a non-existent image
        is requested.
        """
        res = self.get('images/does_not_exist.png')
        self.assertException(res, EntryNotFound)

    def test_get_image_empty_name(self):
        """
        This test ensures that a standard image is returned if no file name
        is requested.
        """
        res = self.get('images/')
        filepath = app.config['UPLOAD_FOLDER'] + 'default.png'
        with open(filepath, 'rb') as image:
            bytes = image.read()
        self.assertEqual(res.data, bytes)
