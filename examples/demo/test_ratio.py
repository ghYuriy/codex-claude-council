import unittest
from ratio import ratio


class RatioTest(unittest.TestCase):
    def test_positive(self):
        self.assertEqual(ratio(2, 4), 0.5)

    def test_zero_denominator(self):
        self.assertIsNone(ratio(1, 0))

    def test_zero_numerator(self):
        self.assertEqual(ratio(0, 4), 0)

    def test_negative(self):
        self.assertEqual(ratio(-2, 4), -0.5)


if __name__ == '__main__':
    unittest.main()
