"""Explicit concrete resource types, extending the engine template."""
from engine.resource import Resource


class Gold(Resource):
    TYPE_ID = 'native:gold'
    NAME_KEY = 'resource.native:gold.name'
    TRADEABLE = False

    @classmethod
    def validate_amount(cls, amount):
        # Currency may represent an overdrawn balance. Consumption still
        # requires available funds; settlement may explicitly set a debt.
        cls.finite_number(amount)

    @classmethod
    def value_of(cls, amount, unit_price):
        # Cash is already the unit of account, not a mined gold output unit.
        cls.finite_number(amount)
        if amount < 0:
            raise ValueError('Consumed currency cannot be negative')
        return amount


class Food(Resource):
    TYPE_ID = 'native:food'
    NAME_KEY = 'resource.native:food.name'


class Wood(Resource):
    TYPE_ID = 'native:wood'
    NAME_KEY = 'resource.native:wood.name'


class Ore(Resource):
    TYPE_ID = 'native:ore'
    NAME_KEY = 'resource.native:ore.name'


class Oil(Resource):
    TYPE_ID = 'native:oil'
    NAME_KEY = 'resource.native:oil.name'


class Equipment(Resource):
    TYPE_ID = 'native:equipment'
    NAME_KEY = 'resource.native:equipment.name'


class Supply(Resource):
    TYPE_ID = 'native:supply'
    NAME_KEY = 'resource.native:supply.name'


RESOURCE_TYPES = (Gold, Food, Wood, Ore, Oil, Equipment, Supply)
