#!/usr/bin/env python3
# -*- coding: utf-8 -*-
__author__ = 'g3n35i5'

from tests.base_api import BaseAPITestCase
from flask import json


class ListTurnoversAPITestCase(BaseAPITestCase):

    def test_list_turnovers_as_admin(self):
        """Test for listing all turnovers as admin"""
        res = self.get(url='/turnovers', role='admin')
        self.assertEqual(res.status_code, 200)
        turnovers = json.loads(res.data)
        self.assertEqual(len(turnovers), 4)
        self.assertEqual(turnovers[0]['amount'], 200)
        self.assertEqual(turnovers[1]['amount'], 100)
        self.assertEqual(turnovers[2]['amount'], -100)
        self.assertEqual(turnovers[3]['amount'], -500)

        required = ['id', 'timestamp', 'amount', 'comment', 'admin_id',
                    'revoked']
        for turnover in turnovers:
            assert all(x in turnover for x in required)
