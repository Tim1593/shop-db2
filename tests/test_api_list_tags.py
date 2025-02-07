#!/usr/bin/env python3
# -*- coding: utf-8 -*-
__author__ = 'g3n35i5'

from tests.base_api import BaseAPITestCase
from tests.base import t_names
from flask import json


class ListTagsAPITestCase(BaseAPITestCase):
    def test_list_tags(self):
        """Test for listing all tags"""
        res = self.get(url='/tags')
        self.assertEqual(res.status_code, 200)
        tags = json.loads(res.data)
        self.assertEqual(len(tags), 4)
        for i in range(4):
            self.assertEqual(tags[i]['name'], t_names[i])
            self.assertEqual(tags[i]['created_by'], 1)
