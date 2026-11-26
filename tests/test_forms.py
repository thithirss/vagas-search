import unittest

from vagas.forms import FormField, fill_field
from vagas.qa import FieldQ


class DateInput:
    def __init__(self):
        self.value = ""
        self.typed = []

    def get_attribute(self, name):
        return "DD/MM/AAAA" if name == "placeholder" else ""

    def click(self):
        pass

    def fill(self, value):
        self.value = value

    def type(self, value, delay=0):
        self.typed.append(value)
        self.value += value

    def input_value(self):
        return self.value

    def evaluate(self, script):
        return False

    def press(self, key):
        pass


class Scope:
    def __init__(self, loc):
        self.loc = loc

    def locator(self, selector):
        return self.loc


class FormFillTests(unittest.TestCase):
    def test_masked_date_keeps_slashes(self):
        loc = DateInput()
        field = FormField(FieldQ("birth", "Data de nascimento", "text"), "#birth", "input", "text")

        self.assertTrue(fill_field(Scope(loc), field, "2002-04-06", None))
        self.assertEqual(loc.value, "06/04/2002")
        self.assertEqual(loc.typed, ["06/04/2002"])


if __name__ == "__main__":
    unittest.main()
