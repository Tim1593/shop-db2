#!/usr/bin/env python3
# -*- coding: utf-8 -*-
__author__ = 'g3n35i5'

import base64
from shopdb.models import *
from shopdb.api import db, app
import shopdb.exceptions as exc
from tests.base_api import BaseAPITestCase
from flask import json
import os


class UpdateProductAPITestCase(BaseAPITestCase):
    def test_update_authorization(self):
        """This route should only be available for administrators"""
        res = self.put(url='/products/2', data={})
        self.assertEqual(res.status_code, 401)
        self.assertException(res, exc.UnauthorizedAccess)
        res = self.put(url='/products/2', data={}, role='user')
        self.assertEqual(res.status_code, 401)
        self.assertException(res, exc.UnauthorizedAccess)
        res = self.put(url='/products/2', data={}, role='admin')
        self.assertEqual(res.status_code, 200)
        self.assertException(res, exc.NothingHasChanged)

    def test_update_forbidden_field(self):
        """Updating a forbidden field should raise an error."""
        self.assertTrue(Product.query.filter_by(id=1).first().creation_date)
        data = {'creation_date': '01.01.1970'}
        res = self.put(url='/products/2', data=data, role='admin')
        self.assertEqual(res.status_code, 401)
        self.assertException(res, exc.ForbiddenField)
        self.assertTrue(Product.query.filter_by(id=1).first().creation_date)

    def test_update_non_existing_product(self):
        """Updating a non existing product should raise an error."""
        data = {'name': 'Bread'}
        res = self.put(url='/products/5', data=data, role='admin')
        self.assertEqual(res.status_code, 401)
        self.assertException(res, exc.EntryNotFound)

    def test_update_wrong_type(self):
        """A wrong field type should raise an error"""
        product1 = Product.query.filter_by(id=1).first()
        data = {'name': True}
        res = self.put(url='/products/1', data=data, role='admin')
        self.assertEqual(res.status_code, 401)
        self.assertException(res, exc.WrongType)
        product2 = Product.query.filter_by(id=1).first()
        self.assertEqual(product1, product2)

    def test_update_unknown_field(self):
        """An unknown field should raise an error"""
        data = {'color': 'red'}
        res = self.put(url='/products/1', data=data, role='admin')
        self.assertEqual(res.status_code, 401)
        self.assertException(res, exc.UnknownField)

    def test_update_product_name(self):
        """Update product name"""
        self.assertEqual(Product.query.filter_by(id=1).first().name, 'Pizza')
        data = {'name': 'Bread'}
        res = self.put(url='/products/1', data=data, role='admin')
        self.assertEqual(res.status_code, 201)
        data = json.loads(res.data)
        self.assertEqual(data['message'], 'Updated product.')
        self.assertEqual(len(data['updated_fields']), 1)
        self.assertEqual(data['updated_fields'][0], 'name')
        self.assertEqual(Product.query.filter_by(id=1).first().name, 'Bread')

    def test_update_product_price(self):
        """Update product price"""
        self.assertEqual(Product.query.filter_by(id=1).first().price, 300)
        pricehist = (ProductPrice.query
                     .filter(ProductPrice.product_id == 1)
                     .all())
        self.assertEqual(len(pricehist), 1)
        self.assertEqual(pricehist[0].price, 300)
        data = {'price': 200}
        res = self.put(url='/products/1', data=data, role='admin')
        self.assertEqual(res.status_code, 201)
        data = json.loads(res.data)
        self.assertEqual(data['message'], 'Updated product.')
        self.assertEqual(len(data['updated_fields']), 1)
        self.assertEqual(data['updated_fields'][0], 'price')
        self.assertEqual(Product.query.filter_by(id=1).first().price, 200)
        pricehist = (ProductPrice.query
                     .filter(ProductPrice.product_id == 1)
                     .all())
        self.assertEqual(len(pricehist), 2)
        self.assertEqual(pricehist[0].price, 300)
        self.assertEqual(pricehist[1].price, 200)

    def test_update_product_image(self):
        """Update the product image"""
        # Upload a product image
        filepath = app.config['UPLOAD_FOLDER'] + 'valid_image.png'
        with open(filepath, 'rb') as test:
            bytes = test.read()
        image = {'filename': 'valid_image.png',
                 'value': base64.b64encode(bytes).decode()}
        res = self.post(url='/upload', data=image, role='admin')
        filename = json.loads(res.data)['filename']
        upload = Upload.query.filter_by(filename=filename).first()
        data = {'imagename': filename}
        res = self.put(url='/products/1', data=data, role='admin')
        self.assertEqual(res.status_code, 201)
        data = json.loads(res.data)
        self.assertEqual(data['message'], 'Updated product.')
        self.assertEqual(len(data['updated_fields']), 1)
        self.assertEqual(data['updated_fields'][0], 'imagename')
        product = Product.query.filter_by(id=1).first()
        self.assertEqual(product.imagename, filename)
        filepath = app.config['UPLOAD_FOLDER'] + filename
        os.remove(filepath)

    def test_update_product_non_existing_image(self):
        """Update the product image with a non existing image should
           raise an error"""
        data = {'imagename': 'test.png'}
        res = self.put(url='/products/1', data=data, role='admin')
        self.assertEqual(res.status_code, 401)
        self.assertException(res, exc.EntryNotFound)

    def test_update_barcode_with_existing_barcode(self):
        """
        It should not be possible to assign a barcode to a product which has
        been assigned to another product.
        """
        Product.query.filter_by(id=1).first().barcode = '123456'
        db.session.commit()
        data = {'barcode': '123456'}
        res = self.put(url='/products/2', data=data, role='admin')
        self.assertException(res, exc.EntryAlreadyExists)
