"""Pays (ISO 3166-1 alpha-2) proposés comme destination des colis fournisseur."""

COUNTRIES = [
    ('AF', 'Afghanistan'), ('ZA', 'Afrique du Sud'), ('AL', 'Albanie'), ('DZ', 'Algérie'), ('DE', 'Allemagne'),
    ('AD', 'Andorre'), ('AO', 'Angola'), ('AI', 'Anguilla'), ('AG', 'Antigua-et-Barbuda'), ('SA', 'Arabie saoudite'),
    ('AR', 'Argentine'), ('AM', 'Arménie'), ('AW', 'Aruba'), ('AU', 'Australie'), ('AT', 'Autriche'),
    ('AZ', 'Azerbaïdjan'), ('BS', 'Bahamas'), ('BH', 'Bahreïn'), ('BD', 'Bangladesh'), ('BB', 'Barbade'),
    ('BE', 'Belgique'), ('BZ', 'Belize'), ('BJ', 'Bénin'), ('BM', 'Bermudes'), ('BT', 'Bhoutan'),
    ('BY', 'Biélorussie'), ('BO', 'Bolivie'), ('BA', 'Bosnie-Herzégovine'), ('BW', 'Botswana'), ('BR', 'Brésil'),
    ('BN', 'Brunei'), ('BG', 'Bulgarie'), ('BF', 'Burkina Faso'), ('BI', 'Burundi'), ('KH', 'Cambodge'),
    ('CM', 'Cameroun'), ('CA', 'Canada'), ('CV', 'Cap-Vert'), ('CL', 'Chili'), ('CN', 'Chine'),
    ('CY', 'Chypre'), ('CO', 'Colombie'), ('KM', 'Comores'), ('CG', 'Congo-Brazzaville'), ('CD', 'Congo-Kinshasa (RDC)'),
    ('KR', 'Corée du Sud'), ('CR', 'Costa Rica'), ('CI', "Côte d'Ivoire"), ('HR', 'Croatie'), ('CU', 'Cuba'),
    ('CW', 'Curaçao'), ('DK', 'Danemark'), ('DJ', 'Djibouti'), ('DM', 'Dominique'), ('EG', 'Égypte'),
    ('AE', 'Émirats arabes unis'), ('EC', 'Équateur'), ('ER', 'Érythrée'), ('ES', 'Espagne'), ('EE', 'Estonie'),
    ('SZ', 'Eswatini'), ('US', 'États-Unis'), ('ET', 'Éthiopie'), ('FJ', 'Fidji'), ('FI', 'Finlande'),
    ('FR', 'France'), ('GA', 'Gabon'), ('GM', 'Gambie'), ('GE', 'Géorgie'), ('GH', 'Ghana'),
    ('GI', 'Gibraltar'), ('GR', 'Grèce'), ('GD', 'Grenade'), ('GL', 'Groenland'), ('GP', 'Guadeloupe'),
    ('GU', 'Guam'), ('GT', 'Guatemala'), ('GN', 'Guinée'), ('GQ', 'Guinée équatoriale'), ('GW', 'Guinée-Bissau'),
    ('GY', 'Guyana'), ('GF', 'Guyane française'), ('HT', 'Haïti'), ('HN', 'Honduras'), ('HK', 'Hong Kong'),
    ('HU', 'Hongrie'), ('IN', 'Inde'), ('ID', 'Indonésie'), ('IQ', 'Irak'), ('IR', 'Iran'),
    ('IE', 'Irlande'), ('IS', 'Islande'), ('IL', 'Israël'), ('IT', 'Italie'), ('JM', 'Jamaïque'),
    ('JP', 'Japon'), ('JO', 'Jordanie'), ('KZ', 'Kazakhstan'), ('KE', 'Kenya'), ('KG', 'Kirghizistan'),
    ('KI', 'Kiribati'), ('XK', 'Kosovo'), ('KW', 'Koweït'), ('LA', 'Laos'), ('LS', 'Lesotho'),
    ('LV', 'Lettonie'), ('LB', 'Liban'), ('LR', 'Liberia'), ('LY', 'Libye'), ('LI', 'Liechtenstein'),
    ('LT', 'Lituanie'), ('LU', 'Luxembourg'), ('MO', 'Macao'), ('MK', 'Macédoine du Nord'), ('MG', 'Madagascar'),
    ('MY', 'Malaisie'), ('MW', 'Malawi'), ('MV', 'Maldives'), ('ML', 'Mali'), ('MT', 'Malte'),
    ('MA', 'Maroc'), ('MH', 'Marshall (îles)'), ('MQ', 'Martinique'), ('MU', 'Maurice'), ('MR', 'Mauritanie'),
    ('YT', 'Mayotte'), ('MX', 'Mexique'), ('FM', 'Micronésie'), ('MD', 'Moldavie'), ('MC', 'Monaco'),
    ('MN', 'Mongolie'), ('ME', 'Monténégro'), ('MS', 'Montserrat'), ('MZ', 'Mozambique'), ('MM', 'Myanmar'),
    ('NA', 'Namibie'), ('NR', 'Nauru'), ('NP', 'Népal'), ('NI', 'Nicaragua'), ('NE', 'Niger'),
    ('NG', 'Nigeria'), ('NO', 'Norvège'), ('NC', 'Nouvelle-Calédonie'), ('NZ', 'Nouvelle-Zélande'), ('OM', 'Oman'),
    ('UG', 'Ouganda'), ('UZ', 'Ouzbékistan'), ('PK', 'Pakistan'), ('PW', 'Palaos'), ('PS', 'Palestine'),
    ('PA', 'Panama'), ('PG', 'Papouasie-Nouvelle-Guinée'), ('PY', 'Paraguay'), ('NL', 'Pays-Bas'), ('PE', 'Pérou'),
    ('PH', 'Philippines'), ('PL', 'Pologne'), ('PF', 'Polynésie française'), ('PR', 'Porto Rico'), ('PT', 'Portugal'),
    ('QA', 'Qatar'), ('CF', 'République centrafricaine'), ('DO', 'République dominicaine'), ('CZ', 'République tchèque'),
    ('RE', 'La Réunion'), ('RO', 'Roumanie'), ('GB', 'Royaume-Uni'), ('RU', 'Russie'), ('RW', 'Rwanda'),
    ('KN', 'Saint-Kitts-et-Nevis'), ('SM', 'Saint-Marin'), ('PM', 'Saint-Pierre-et-Miquelon'),
    ('VC', 'Saint-Vincent-et-les-Grenadines'), ('LC', 'Sainte-Lucie'), ('SB', 'Salomon (îles)'), ('SV', 'Salvador'),
    ('WS', 'Samoa'), ('ST', 'Sao Tomé-et-Principe'), ('SN', 'Sénégal'), ('RS', 'Serbie'), ('SC', 'Seychelles'),
    ('SL', 'Sierra Leone'), ('SG', 'Singapour'), ('SK', 'Slovaquie'), ('SI', 'Slovénie'), ('SO', 'Somalie'),
    ('SD', 'Soudan'), ('SS', 'Soudan du Sud'), ('LK', 'Sri Lanka'), ('SE', 'Suède'), ('CH', 'Suisse'),
    ('SR', 'Suriname'), ('SY', 'Syrie'), ('TJ', 'Tadjikistan'), ('TW', 'Taïwan'), ('TZ', 'Tanzanie'),
    ('TD', 'Tchad'), ('TH', 'Thaïlande'), ('TL', 'Timor oriental'), ('TG', 'Togo'), ('TO', 'Tonga'),
    ('TT', 'Trinité-et-Tobago'), ('TN', 'Tunisie'), ('TM', 'Turkménistan'), ('TR', 'Turquie'), ('TV', 'Tuvalu'),
    ('UA', 'Ukraine'), ('UY', 'Uruguay'), ('VU', 'Vanuatu'), ('VE', 'Venezuela'), ('VN', 'Viêt Nam'),
    ('WF', 'Wallis-et-Futuna'), ('YE', 'Yémen'), ('ZM', 'Zambie'), ('ZW', 'Zimbabwe'),
]

# Proposés en tête de liste : zone CEMAC, puis Afrique de l'Ouest et voisins
FREQUENT = ['CM', 'GA', 'CG', 'TD', 'CF', 'GQ', 'NG', 'CD', 'CI', 'SN', 'BJ', 'TG', 'BF', 'ML', 'NE', 'GN', 'GH', 'FR', 'BE']

NAMES = dict(COUNTRIES)


def country_name(code):
    return NAMES.get((code or '').upper(), code or '')
