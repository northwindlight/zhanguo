"""Model-facing content synonyms.

Aliases are registered into ``ContentRegistry`` at tool-layer build time. They
cover the Chinese display names, receipt shorthands and common English plurals
that the model reads in panels/receipts. The canonical registry still stores
only namespaced IDs.
"""

CONTENT_ALIASES = {
    # resources
    '黄金': 'native:gold', '金': 'native:gold', 'money': 'native:gold',
    '粮食': 'native:food', '粮': 'native:food', 'grain': 'native:food',
    '木头': 'native:wood', '木': 'native:wood', 'lumber': 'native:wood',
    '矿石': 'native:ore', '矿': 'native:ore', 'mineral': 'native:ore',
    '石油': 'native:oil', '油': 'native:oil', 'petroleum': 'native:oil',
    '装备': 'native:equipment', '装': 'native:equipment',
    'equip': 'native:equipment', 'weapon': 'native:equipment',
    'weapons': 'native:equipment',
    '补给': 'native:supply', '补': 'native:supply',
    'supply': 'native:supply', 'supplies': 'native:supply',
    # buildings
    '城堡': 'native:castle',
    '林场': 'native:lumber-camp',
    '农场': 'native:farm',
    '矿场': 'native:mine',
    '石油厂': 'native:oil-well',
    '黄金矿场': 'native:gold-mine',
    '木材能源厂': 'native:wood-power-plant',
    '石油能源厂': 'native:oil-power-plant',
    '补给厂': 'native:supply-factory',
    '装备厂': 'native:equipment-factory',
    '兵营': 'native:barracks',
    '市政厅': 'native:town-hall',
    '瞭望塔': 'native:watchtower',
    '外交中心': 'native:diplomacy-center',
    '工程院': 'native:academy',
    '军屯': 'native:militia-camp',
    # units
    '步兵': 'native:infantry', '步': 'native:infantry',
    '骑兵': 'native:cavalry', '骑': 'native:cavalry',
    '民兵': 'native:militia', '民': 'native:militia',
}
