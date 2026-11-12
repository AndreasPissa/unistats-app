"""Regression for signed percentage-point rounding across RDF engines."""
import asyncio
import unittest
from decimal import Decimal, ROUND_FLOOR
from backend.database import Database
from backend.catalogue import QUERIES

class QueryRoundingTests(unittest.TestCase):
    def test_gapri_signed_change_matches_independent_calculation(self):
        database=Database()
        try:
            rows=database.local(QUERIES['X06_gapri'])['rows']
            negative=0
            for row in rows:
                change=Decimal(row['phdFemalePct'])-Decimal(row['bscFemalePct'])
                expected=(change*100+Decimal('0.5')).to_integral_value(rounding=ROUND_FLOOR)/100
                self.assertEqual(Decimal(row['femaleShareChangePoints']),expected)
                negative+=change<0
            self.assertGreater(negative,0)
        finally:
            asyncio.run(database.close())
