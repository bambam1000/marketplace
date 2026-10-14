"""Catalogue de démonstration : de vrais produits avec photos dans les 6 boutiques de démo.

Photos : catalogue libre DummyJSON (https://dummyjson.com), prévu pour les démonstrations et les prototypes.
Pour un site en production, remplacez-les par vos propres photos ou celles de vos fournisseurs.

    python manage.py seed_demo_catalog              # crée / met à jour les produits (idempotent)
    python manage.py seed_demo_catalog --keep-old   # sans archiver les anciens produits de démo sans photo
"""
import json
import random
from datetime import timedelta
from pathlib import Path
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from catalog.models import Category, Product, Review
from store.models import Store

API = 'https://dummyjson.com/products?limit=0&select=images'
SKU_PREFIX = 'DEMO-'

# (n° DummyJSON, boutique (son nom, ou « user:<identifiant> » pour la boutique d'un vendeur), catégorie, nom,
#  description, prix, ancien prix, stock, options)
# options : flash, featured, colors, sizes, specs, origin, min_order
P = lambda *a, **kw: (a, kw)  # noqa: E731
CATALOG = [
    # ── TechCameroun : smartphones, ordinateurs, audio, accessoires ──
    P(123, 'TechCameroun', 'smartphones', 'iPhone 13 Pro 128 Go',
      "Écran Super Retina XDR 6,1 pouces avec ProMotion, triple appareil photo 12 Mpx et puce A15 Bionic. Garantie 6 mois en boutique.",
      485000, 540000, 8, featured=True, colors='Graphite, Argent, Or, Bleu alpin', specs='Écran: 6,1 pouces\nStockage: 128 Go\nAppareil photo: triple 12 Mpx\nPuce: A15 Bionic', origin='États-Unis'),
    P(124, 'TechCameroun', 'smartphones', 'iPhone X 64 Go (reconditionné)',
      "iPhone X reconditionné, testé et nettoyé, batterie contrôlée au-dessus de 85 %. Livré avec chargeur et garantie 3 mois.",
      165000, 195000, 12, flash=True, colors='Gris sidéral, Argent', specs='Écran: 5,8 pouces OLED\nStockage: 64 Go\nÉtat: reconditionné grade A', origin='États-Unis'),
    P(133, 'TechCameroun', 'smartphones', 'Samsung Galaxy S10 128 Go',
      "Écran Dynamic AMOLED 6,1 pouces, triple capteur photo et lecteur d'empreintes sous l'écran. Double SIM.",
      235000, None, 10, colors='Noir prisme, Blanc prisme, Vert', specs='Écran: 6,1 pouces\nStockage: 128 Go\nRAM: 8 Go\nDouble SIM: oui', origin='Corée du Sud'),
    P(132, 'TechCameroun', 'smartphones', 'Samsung Galaxy S8 64 Go',
      "Le format Infinity Display de Samsung à petit prix : écran incurvé 5,8 pouces, étanche IP68.",
      115000, 135000, 14, colors='Noir, Argent orchidée', specs='Écran: 5,8 pouces\nStockage: 64 Go\nÉtanchéité: IP68', origin='Corée du Sud'),
    P(126, 'TechCameroun', 'smartphones', 'Oppo F19 Pro+ 5G',
      "Charge rapide 50 W (pleine charge en 48 minutes), quadruple appareil photo 48 Mpx et écran AMOLED 6,4 pouces.",
      175000, 199000, 15, flash=True, colors='Noir fluide, Argent spatial', specs='Écran: 6,4 pouces AMOLED\nStockage: 128 Go\nRAM: 8 Go\nCharge: 50 W', origin='Chine'),
    P(128, 'TechCameroun', 'smartphones', 'Realme C35 64 Go',
      "Grand écran 6,6 pouces, batterie 5000 mAh qui tient deux jours et appareil photo 50 Mpx. Le meilleur rapport qualité-prix.",
      89000, None, 30, colors='Noir brillant, Vert brillant', specs='Écran: 6,6 pouces\nBatterie: 5000 mAh\nAppareil photo: 50 Mpx', origin='Chine'),
    P(135, 'TechCameroun', 'smartphones', 'Vivo V9 64 Go',
      "Écran 6,3 pouces sans bordure et caméra selfie 24 Mpx avec mode portrait. Double SIM + carte mémoire.",
      98000, 110000, 9, colors='Noir, Or champagne', specs='Écran: 6,3 pouces\nStockage: 64 Go\nSelfie: 24 Mpx', origin='Chine'),
    P(78, 'TechCameroun', 'ordinateurs', 'Apple MacBook Pro 14 pouces M1 Pro',
      "Puce M1 Pro, écran Liquid Retina XDR et jusqu'à 17 heures d'autonomie. Pour le montage vidéo, le design et le développement.",
      1150000, 1250000, 3, featured=True, colors='Gris sidéral, Argent', specs='Écran: 14,2 pouces\nPuce: Apple M1 Pro\nRAM: 16 Go\nSSD: 512 Go', origin='États-Unis'),
    P(82, 'TechCameroun', 'ordinateurs', 'Dell XPS 13 9300',
      "Ultraportable de 1,2 kg avec écran InfinityEdge 13,4 pouces, processeur Intel Core i7 et SSD 512 Go.",
      825000, None, 4, specs='Écran: 13,4 pouces\nProcesseur: Intel Core i7\nRAM: 16 Go\nSSD: 512 Go', origin='États-Unis'),
    P(80, 'TechCameroun', 'ordinateurs', 'Huawei MateBook X Pro',
      "Écran tactile 3K 13,9 pouces, clavier rétroéclairé et lecteur d'empreintes dans le bouton d'alimentation.",
      745000, 790000, 5, specs='Écran: 13,9 pouces 3K tactile\nProcesseur: Intel Core i7\nSSD: 1 To', origin='Chine'),
    P(81, 'TechCameroun', 'ordinateurs', 'Lenovo Yoga 920 convertible',
      "PC portable 2-en-1 : écran tactile qui se retourne à 360° pour passer en mode tablette. Stylet compatible.",
      560000, None, 6, specs='Écran: 13,9 pouces tactile\nRAM: 8 Go\nSSD: 256 Go', origin='Chine'),
    P(159, 'TechCameroun', 'ordinateurs', 'iPad mini 2021 64 Go Wi-Fi',
      "Écran Liquid Retina 8,3 pouces, puce A15 Bionic et compatibilité Apple Pencil 2. Tient dans une main.",
      310000, 345000, 7, colors='Lumière stellaire, Gris sidéral, Rose, Mauve', specs='Écran: 8,3 pouces\nStockage: 64 Go\nPuce: A15 Bionic', origin='États-Unis'),
    P(160, 'TechCameroun', 'ordinateurs', 'Samsung Galaxy Tab S8+ 128 Go',
      "Grande tablette AMOLED 12,4 pouces avec stylet S Pen fourni : idéale pour prendre des notes et travailler en déplacement.",
      385000, None, 5, specs='Écran: 12,4 pouces AMOLED\nStockage: 128 Go\nStylet: S Pen inclus', origin='Corée du Sud'),
    P(100, 'TechCameroun', 'accessoires-tech', 'Apple AirPods (2e génération)',
      "Écouteurs sans fil avec boîtier de charge : appairage instantané avec l'iPhone et jusqu'à 24 heures d'écoute.",
      75000, 89000, 25, flash=True, specs="Autonomie: 5 h (24 h avec le boîtier)\nConnexion: Bluetooth", origin='États-Unis'),
    P(101, 'TechCameroun', 'tv-audio', 'Apple AirPods Max',
      "Casque à réduction de bruit active, audio spatial et 20 heures d'autonomie. Coussinets en mousse à mémoire de forme.",
      345000, None, 4, featured=True, colors='Argent, Gris sidéral, Bleu ciel', specs='Réduction de bruit: active\nAutonomie: 20 h', origin='États-Unis'),
    P(107, 'TechCameroun', 'tv-audio', 'Écouteurs Beats Flex sans fil',
      "Écouteurs tour de cou Bluetooth, 12 heures d'autonomie et embouts aimantés qui se rangent autour du cou.",
      32000, 38000, 40, colors='Noir, Bleu, Gris', specs='Autonomie: 12 h\nConnexion: Bluetooth', origin='États-Unis'),
    P(103, 'TechCameroun', 'tv-audio', 'Enceinte Apple HomePod mini',
      "Petite enceinte connectée au son à 360°, idéale pour la chambre ou le bureau.",
      65000, None, 10, colors='Gris sidéral, Blanc', origin='États-Unis'),
    P(106, 'TechCameroun', 'accessoires-tech', 'Apple Watch Series 4 Or',
      "Montre connectée : suivi du sport et du sommeil, notifications et appels depuis le poignet.",
      155000, 180000, 6, specs='Boîtier: 40 mm\nÉtanchéité: 50 m', origin='États-Unis'),
    P(105, 'TechCameroun', 'accessoires-tech', 'Batterie externe Apple MagSafe',
      "Se fixe au dos de l'iPhone par aimant et le recharge sans fil, sans câble.",
      55000, None, 15, origin='États-Unis'),
    P(104, 'TechCameroun', 'accessoires-tech', 'Chargeur rapide iPhone 20 W USB-C',
      "Adaptateur secteur 20 W : recharge un iPhone à 50 % en 30 minutes. Câble vendu séparément.",
      9500, 12000, 80, specs='Puissance: 20 W\nPort: USB-C', origin='Chine'),
    P(111, 'TechCameroun', 'accessoires-tech', 'Perche à selfie trépied Bluetooth',
      "Perche extensible jusqu'à 1 m, se transforme en trépied, avec télécommande Bluetooth.",
      7500, None, 60, origin='Chine'),
    # ── ModAfrica : mode homme, femme, chaussures, sacs ──
    P(85, 'ModAfrica', 'mode-homme', 'Chemise homme à carreaux',
      "Chemise en coton à manches longues, coupe ajustée. Se porte au bureau comme le week-end.",
      12500, 15000, 35, flash=True, colors='Rouge, Bleu', sizes='S, M, L, XL, XXL'),
    P(83, 'ModAfrica', 'mode-homme', 'Chemise carreaux bleu et noir',
      "Chemise décontractée en flanelle douce, boutons nacrés et poche poitrine.",
      11000, None, 28, sizes='M, L, XL'),
    P(86, 'ModAfrica', 'mode-homme', 'Chemise homme manches courtes',
      "Chemise légère et respirante, parfaite pour la chaleur de Douala.",
      9000, None, 40, colors='Blanc, Bleu ciel', sizes='S, M, L, XL'),
    P(162, 'ModAfrica', 'mode-femme', 'Robe évasée bleue',
      "Robe fluide à taille marquée, longueur genou. Tissu léger qui ne se froisse pas.",
      16500, 19500, 20, featured=True, sizes='S, M, L'),
    P(163, 'ModAfrica', 'mode-femme', "Robe d'été fille",
      "Robe à fleurs légère pour les beaux jours, en coton doux.",
      8500, None, 25, sizes='4 ans, 6 ans, 8 ans, 10 ans'),
    P(177, 'ModAfrica', 'mode-femme', 'Robe de soirée longue noire',
      "Robe longue élégante pour mariages, galas et cérémonies. Fermeture éclair invisible au dos.",
      45000, 55000, 8, flash=True, sizes='S, M, L, XL'),
    P(180, 'ModAfrica', 'mode-femme', 'Robe verte cintrée',
      "Robe midi cintrée, coupe flatteuse. Se porte avec talons ou baskets.",
      19000, None, 14, sizes='S, M, L'),
    P(88, 'ModAfrica', 'chaussures', 'Nike Air Jordan 1 rouge et noir',
      "La basket mythique en cuir, coloris « Chicago » rouge et noir. Semelle Air pour l'amorti.",
      95000, 110000, 10, featured=True, sizes='40, 41, 42, 43, 44, 45', origin='États-Unis'),
    P(186, 'ModAfrica', 'chaussures', 'Escarpins Calvin Klein',
      "Escarpins à talon de 9 cm en similicuir verni, bout pointu. Élégants pour le bureau et les sorties.",
      38000, None, 12, sizes='36, 37, 38, 39, 40', origin='États-Unis'),
    P(187, 'ModAfrica', 'chaussures', 'Sandales dorées femme',
      "Sandales à petit talon avec brides dorées, confortables toute la journée.",
      18000, 22000, 18, sizes='36, 37, 38, 39, 40, 41'),
    P(173, 'ModAfrica', 'sacs-accessoires', 'Sac à main en cuir Heshe',
      "Sac en cuir véritable, grand compartiment zippé et bandoulière amovible.",
      62000, 75000, 7, colors='Marron, Noir'),
    P(172, 'ModAfrica', 'sacs-accessoires', 'Sac à main bleu femme',
      "Sac à main structuré avec poignées et bandoulière, poches intérieures pour téléphone et clés.",
      24000, None, 15),
    P(175, 'ModAfrica', 'sacs-accessoires', 'Sac à dos blanc similicuir',
      "Sac à dos urbain en similicuir, idéal pour l'université et le travail.",
      19500, None, 20),
    P(93, 'ModAfrica', 'sacs-accessoires', 'Montre homme bracelet cuir marron',
      "Montre à quartz, cadran sobre et bracelet en cuir véritable. Étanche aux éclaboussures.",
      27000, 32000, 16),
    P(154, 'ModAfrica', 'sacs-accessoires', 'Lunettes de soleil noires',
      "Lunettes de soleil protection UV400, monture légère et résistante.",
      9000, None, 45),
    P(182, 'ModAfrica', 'sacs-accessoires', "Boucles d'oreilles cristal vert",
      "Boucles d'oreilles pendantes en cristal vert, pour sublimer une tenue de soirée.",
      7500, None, 30),
    # ── MaisonPlus : meubles, cuisine, électroménager ──
    P(12, 'MaisonPlus', 'meubles', 'Canapé 3 places en velours',
      "Canapé confortable en velours, assise moelleuse et pieds en bois massif. Livraison et montage à Douala et Yaoundé.",
      650000, 720000, 3, featured=True, colors='Gris, Bleu nuit', specs='Places: 3\nDimensions: 220 x 90 x 85 cm', origin='Italie'),
    P(13, 'MaisonPlus', 'meubles', 'Table de chevet en bois',
      "Table de chevet en bois massif avec tiroir, finition naturelle.",
      85000, None, 10, specs='Matière: bois massif\nDimensions: 45 x 40 x 55 cm', origin='Cameroun'),
    P(14, 'MaisonPlus', 'meubles', 'Fauteuil de bureau direction',
      "Fauteuil de direction ergonomique, assise rembourrée, pivotant à 360°.",
      145000, 165000, 6, specs='Assise: pivotante\nPiètement: aluminium', origin='États-Unis'),
    P(47, 'MaisonPlus', 'meubles', 'Lampe de table design',
      "Lampe de chevet à abat-jour en tissu, lumière douce et chaleureuse.",
      22000, None, 18),
    P(46, 'MaisonPlus', 'meubles', 'Pot de fleurs décoratif',
      "Pot en céramique pour plantes d'intérieur, avec soucoupe.",
      8000, 10000, 30, flash=True),
    P(51, 'MaisonPlus', 'electromenager', 'Blender 1,5 L',
      "Mixeur puissant pour jus, smoothies, sauces et purées. Bol en verre de 1,5 litre.",
      28000, 33000, 20, featured=True, specs='Capacité: 1,5 L\nVitesses: 3 + impulsion', origin='Chine'),
    P(66, 'MaisonPlus', 'electromenager', 'Four micro-ondes 20 L',
      "Micro-ondes 700 W avec gril, 5 niveaux de puissance et minuterie.",
      62000, None, 9, specs='Capacité: 20 L\nPuissance: 700 W', origin='Chine'),
    P(56, 'MaisonPlus', 'electromenager', 'Plaque de cuisson électrique',
      "Plaque électrique simple, chauffe rapidement, thermostat réglable. Pratique pour studio ou petit appartement.",
      18500, 22000, 25, flash=True, specs='Puissance: 1500 W', origin='Chine'),
    P(61, 'MaisonPlus', 'electromenager', 'Mixeur plongeant',
      "Mixeur plongeant avec pied inox, pour soupes et sauces directement dans la casserole.",
      16000, None, 22, origin='Chine'),
    P(52, 'MaisonPlus', 'cuisine', 'Wok en acier carbone',
      "Wok de 32 cm pour sautés et fritures, compatible tous feux.",
      15000, None, 30),
    P(71, 'MaisonPlus', 'cuisine', 'Marmite inox avec couvercle en verre',
      "Marmite en inox à fond épais, couvercle en verre pour surveiller la cuisson. Idéale pour le ndolé et les sauces.",
      21000, 25000, 24),
    P(68, 'MaisonPlus', 'cuisine', 'Poêle antiadhésive 28 cm',
      "Poêle antiadhésive, manche qui ne chauffe pas. Cuisson saine avec peu d'huile.",
      12500, None, 35),
    P(64, 'MaisonPlus', 'cuisine', 'Couteau de chef inox',
      "Couteau de cuisine lame inox 20 cm, tranchant durable.",
      8500, None, 50),
    P(65, 'MaisonPlus', 'cuisine', 'Boîte à repas isotherme',
      "Lunch box étanche à compartiments pour emporter son repas au travail ou à l'école.",
      6500, 8000, 60),
    # ── BeautyQueen : maquillage, parfums, soins ──
    P(8, 'BeautyQueen', 'parfums', "Dior J'adore Eau de Parfum 100 ml",
      "Parfum floral féminin aux notes d'ylang-ylang, de rose de Damas et de jasmin. Produit authentique.",
      78000, 89000, 10, featured=True, origin='France'),
    P(7, 'BeautyQueen', 'parfums', 'Chanel Coco Noir Eau de Parfum 100 ml',
      "Parfum oriental boisé, élégant et mystérieux, aux notes de pamplemousse, rose et patchouli.",
      95000, None, 6, origin='France'),
    P(6, 'BeautyQueen', 'parfums', 'Calvin Klein CK One 100 ml',
      "Eau de toilette mixte fraîche et légère, aux notes d'agrumes et de thé vert.",
      32000, 38000, 18, flash=True, origin='États-Unis'),
    P(10, 'BeautyQueen', 'parfums', 'Gucci Bloom Eau de Parfum 100 ml',
      "Parfum floral blanc à la tubéreuse et au jasmin, riche et lumineux.",
      69000, None, 8, origin='Italie'),
    P(1, 'BeautyQueen', 'maquillage', 'Mascara Essence Lash Princess',
      "Mascara volume et longueur, brosse conique, tient toute la journée sans couler.",
      4500, 5500, 80, flash=True),
    P(2, 'BeautyQueen', 'maquillage', 'Palette de fards à paupières avec miroir',
      "Palette de 12 teintes mates et irisées, du nude au plus intense. Miroir intégré.",
      9500, None, 40),
    P(4, 'BeautyQueen', 'maquillage', 'Rouge à lèvres rouge mat',
      "Rouge à lèvres longue tenue, fini mat velouté, texture confortable.",
      5000, None, 70),
    P(5, 'BeautyQueen', 'maquillage', 'Vernis à ongles rouge',
      "Vernis brillant à séchage rapide, couleur intense en deux couches.",
      3000, None, 90),
    P(119, 'BeautyQueen', 'soins-visage', 'Gel douche Olay au beurre de karité',
      "Gel douche ultra-hydratant au beurre de karité, pour une peau douce et nourrie.",
      6500, None, 50, origin='États-Unis'),
    P(120, 'BeautyQueen', 'soins-visage', 'Lotion Vaseline Men visage et corps',
      "Lotion hydratante non grasse pour homme, pénètre vite et protège la peau du dessèchement.",
      5500, 6500, 45, origin='États-Unis'),
    # ── AgroKing : alimentation ──
    P(27, 'AgroKing', 'miel', "Miel naturel de l'Adamaoua 500 g",
      "Miel pur récolté dans l'Adamaoua, non chauffé et sans sucre ajouté. Riche et parfumé.",
      4500, None, 60, featured=True, origin='Cameroun'),
    P(34, 'AgroKing', 'cafe-the', 'Café soluble Nescafé Classic 200 g',
      "Café soluble au goût intense, prêt en quelques secondes.",
      5500, 6500, 70, flash=True, origin='Côte d\'Ivoire'),
    P(26, 'AgroKing', 'epices', 'Piment vert frais (1 kg)',
      "Piment vert frais du marché, cueilli dans la semaine. Vendu au kilo.",
      2000, None, 40, origin='Cameroun', min_order=2),
    P(25, 'AgroKing', 'epices', 'Poivron vert frais (1 kg)',
      "Poivrons verts croquants pour vos sauces, salades et brochettes.",
      1800, None, 40, origin='Cameroun'),
    P(38, 'AgroKing', 'alimentation', 'Riz parfumé long grain (25 kg)',
      "Sac de riz parfumé long grain de 25 kg, grains entiers qui ne collent pas. Prix de gros dès 5 sacs.",
      21000, 23500, 100, origin='Thaïlande', min_order=1),
    P(20, 'AgroKing', 'alimentation', 'Huile végétale raffinée (5 L)',
      "Bidon d'huile végétale de 5 litres pour la friture et la cuisine de tous les jours.",
      8500, None, 80, origin='Cameroun'),
    P(37, 'AgroKing', 'alimentation', 'Oignons rouges (5 kg)',
      "Filet de 5 kg d'oignons rouges, saveur douce et piquante.",
      3500, None, 50, origin='Cameroun'),
    P(35, 'AgroKing', 'alimentation', "Pommes de terre de l'Ouest (5 kg)",
      "Pommes de terre fermes de la région de l'Ouest, parfaites frites ou en ragoût.",
      3000, None, 50, origin='Cameroun'),
    # ── SportZone : sport, fitness, auto-moto ──
    P(147, 'SportZone', 'football', 'Ballon de football taille 5',
      "Ballon de match cousu main, taille 5 officielle. Pour le terrain et le gazon synthétique.",
      9500, 12000, 50, flash=True),
    P(140, 'SportZone', 'football', 'Ballon de basketball taille 7',
      "Ballon en caoutchouc à bonne adhérence, pour l'intérieur comme l'extérieur.",
      8500, None, 30),
    P(153, 'SportZone', 'football', 'Ballon de volleyball',
      "Ballon de volley doux au toucher, pour la plage et le gymnase.",
      7000, None, 25),
    P(152, 'SportZone', 'fitness', 'Raquette de tennis',
      "Raquette légère en alliage, tamis large pour les débutants et les joueurs loisirs.",
      21000, 25000, 12),
    P(36, 'SportZone', 'fitness', 'Protéine en poudre 1 kg',
      "Protéine de lactosérum pour la récupération et la prise de muscle. Saveur vanille.",
      18500, None, 25, featured=True),
    P(90, 'SportZone', 'vetements-sport', 'Baskets Puma Future Rider',
      "Baskets rétro confortables, semelle légère et amortie pour la course et la ville.",
      52000, 60000, 14, sizes='39, 40, 41, 42, 43, 44', origin='Allemagne'),
    P(91, 'SportZone', 'vetements-sport', 'Baskets de sport blanc et rouge',
      "Baskets de running respirantes à semelle amortissante.",
      38000, None, 18, sizes='40, 41, 42, 43, 44, 45'),
    P(84, 'SportZone', 'vetements-sport', 'T-shirt de sport homme',
      "T-shirt technique en tissu respirant qui sèche vite, pour l'entraînement.",
      7500, None, 60, colors='Noir, Blanc', sizes='S, M, L, XL'),
    P(116, 'SportZone', 'auto-moto', 'Scooter 125 cm³',
      "Scooter urbain économique, idéal pour les trajets en ville. Coffre sous la selle.",
      780000, 850000, 3, specs='Cylindrée: 125 cm³\nConsommation: 2,5 L / 100 km', origin='Chine'),
    P(113, 'SportZone', 'auto-moto', 'Moto 150 cm³',
      "Moto robuste et fiable pour la ville et les pistes, entretien simple et pièces disponibles.",
      950000, None, 2, specs='Cylindrée: 150 cm³\nBoîte: 5 vitesses', origin='Chine'),
    # ── Boutique du vendeur test1 (« user:<identifiant> » = boutique de ce vendeur) : téléphones et accessoires.
    #    Essai gratuit : 15 produits au plus, ni vente flash ni produit mis en avant ──
    P(131, 'user:test1', 'smartphones', 'Samsung Galaxy S7 32 Go',
      "Écran Super AMOLED 5,1 pouces, étanche IP68 et appareil photo 12 Mpx très lumineux. Un classique fiable à petit prix.",
      75000, 85000, 10, colors='Noir onyx, Or platine', specs='Écran: 5,1 pouces\nStockage: 32 Go\nÉtanchéité: IP68', origin='Corée du Sud'),
    P(122, 'user:test1', 'smartphones', 'iPhone 6 32 Go (reconditionné)',
      "iPhone 6 reconditionné et testé, batterie contrôlée. Idéal comme premier smartphone ou téléphone de secours.",
      62000, None, 8, colors='Gris sidéral, Or', specs='Écran: 4,7 pouces\nStockage: 32 Go\nÉtat: reconditionné grade A', origin='États-Unis'),
    P(125, 'user:test1', 'smartphones', 'Oppo A57 64 Go',
      "Grande batterie 5000 mAh avec charge rapide 33 W, écran 6,56 pouces et haut-parleurs stéréo.",
      92000, None, 15, colors='Noir lumineux, Vert scintillant', specs='Écran: 6,56 pouces\nBatterie: 5000 mAh\nCharge: 33 W', origin='Chine'),
    P(127, 'user:test1', 'smartphones', 'Oppo K1 64 Go',
      "Lecteur d'empreintes sous l'écran, écran AMOLED 6,4 pouces et caméra selfie 25 Mpx.",
      105000, 119000, 9, colors='Bleu, Rouge', specs='Écran: 6,4 pouces AMOLED\nStockage: 64 Go\nSelfie: 25 Mpx', origin='Chine'),
    P(129, 'user:test1', 'smartphones', 'Realme X 128 Go',
      "Écran plein sans encoche grâce à la caméra selfie escamotable, capteur principal 48 Mpx.",
      118000, None, 7, colors='Blanc polaire, Bleu', specs='Écran: 6,53 pouces\nStockage: 128 Go\nAppareil photo: 48 Mpx', origin='Chine'),
    P(130, 'user:test1', 'smartphones', 'Realme XT 128 Go',
      "Quadruple appareil photo 64 Mpx et charge rapide 20 W. Excellent pour les photos de famille et les réseaux sociaux.",
      135000, 150000, 6, colors='Blanc perle, Bleu perle', specs='Appareil photo: 64 Mpx\nStockage: 128 Go\nCharge: 20 W', origin='Chine'),
    P(134, 'user:test1', 'smartphones', 'Vivo S1 128 Go',
      "Écran AMOLED 6,38 pouces, triple appareil photo et batterie 4500 mAh. Design fin et léger.",
      110000, None, 8, colors='Bleu Skyline, Diamant noir', specs='Écran: 6,38 pouces\nStockage: 128 Go\nBatterie: 4500 mAh', origin='Chine'),
    P(136, 'user:test1', 'smartphones', 'Vivo X21 128 Go',
      "Lecteur d'empreintes sous l'écran et double appareil photo, pour de belles photos même le soir.",
      155000, 175000, 5, colors='Noir, Rouge rubis', specs='Écran: 6,28 pouces\nStockage: 128 Go\nRAM: 6 Go', origin='Chine'),
    P(161, 'user:test1', 'ordinateurs', 'Samsung Galaxy Tab 10,1 pouces blanche',
      "Tablette 10,1 pouces pour les cours en ligne, les films et la lecture. Fente pour carte mémoire.",
      165000, None, 6, specs='Écran: 10,1 pouces\nStockage: 32 Go\nCarte mémoire: oui', origin='Corée du Sud'),
    P(99, 'user:test1', 'tv-audio', 'Enceinte connectée Amazon Echo Plus',
      "Enceinte intelligente au son clair : musique, radio et minuteur à la voix.",
      58000, 65000, 5, origin='États-Unis'),
    P(102, 'user:test1', 'accessoires-tech', 'Chargeur sans fil à induction',
      "Socle de charge sans fil pour smartphones compatibles Qi : posez votre téléphone, il se recharge.",
      14500, 18000, 25, origin='Chine'),
    P(108, 'user:test1', 'accessoires-tech', 'Coque silicone MagSafe iPhone 12',
      "Coque en silicone doux au toucher, aimants MagSafe intégrés, protège des chocs et des rayures.",
      8500, None, 40, colors='Prune, Noir, Bleu'),
    P(110, 'user:test1', 'accessoires-tech', 'Lampe anneau LED pour selfie',
      "Anneau lumineux à pince pour téléphone : 3 températures de lumière pour les vidéos, lives et appels.",
      6000, None, 50),
    P(194, 'user:test1', 'sacs-accessoires', 'Montre bracelet femme',
      "Montre élégante à cadran fin et bracelet métallique, se porte au quotidien comme en soirée.",
      29000, 35000, 12),
]

REVIEWERS = ['Aïcha M.', 'Jean-Paul K.', 'Christelle N.', 'Serge T.', 'Mireille F.', 'Patrick E.', 'Nadège B.', 'Hervé D.']
COMMENTS = [
    (5, 'Très bon produit, conforme à la description. Livré rapidement à Douala.'),
    (5, 'Excellent rapport qualité-prix, je recommande cette boutique.'),
    (4, 'Bon produit, la livraison a pris un jour de plus que prévu mais le vendeur a prévenu.'),
    (5, 'Vendeur sérieux et réactif sur WhatsApp. Rien à redire.'),
    (4, 'Satisfait de mon achat, emballage soigné.'),
    (3, "Correct pour le prix, sans plus."),
]


class Command(BaseCommand):
    help = 'Remplit les boutiques de démonstration avec de vrais produits et leurs photos.'

    def add_arguments(self, parser):
        parser.add_argument('--keep-old', action='store_true',
                            help="Ne pas archiver les anciens produits de démo sans photo.")

    def handle(self, *args, keep_old=False, **options):
        self.stdout.write('Téléchargement de la liste des photos…')
        images = {p['id']: p['images'] for p in self._json(API)['products']}
        wanted = {item[0][1] for item in CATALOG}
        stores = {s.name: s for s in Store.objects.filter(name__in=wanted)}
        for key in (k for k in wanted if k.startswith('user:')):  # boutique désignée par son propriétaire
            store = Store.objects.filter(owner__username=key[5:]).order_by('pk').first()
            if store:
                stores[key] = store
        categories = {c.slug: c for c in Category.objects.all()}
        folder = Path(settings.MEDIA_ROOT) / 'products' / 'demo'
        folder.mkdir(parents=True, exist_ok=True)
        rng = random.Random(42)
        created = updated = skipped = 0

        for (dj_id, store_name, cat_slug, name, desc, price, old_price, stock), opts in CATALOG:
            store, category = stores.get(store_name), categories.get(cat_slug)
            if store is None or category is None:
                self.stdout.write(self.style.WARNING(f'Ignoré « {name} » : boutique « {store_name} » ou catégorie « {cat_slug} » absente.'))
                skipped += 1
                continue
            files = [self._download(url, folder / f'{dj_id}-{n}{Path(url).suffix or ".webp"}')
                     for n, url in enumerate(images.get(dj_id, [])[:4], start=1)]
            files = [f for f in files if f]
            with transaction.atomic():
                product = Product.objects.filter(sku=f'{SKU_PREFIX}{dj_id}', store=store).first()
                is_new = product is None
                product = product or Product(store=store, sku=f'{SKU_PREFIX}{dj_id}', stock=0)
                product.category, product.name, product.description = category, name, desc
                product.price, product.old_price = price, old_price
                product.cost_price = int(price * 0.72 / 100) * 100
                product.colors, product.sizes = opts.get('colors', ''), opts.get('sizes', '')
                product.specifications = opts.get('specs', '')
                product.origin = opts.get('origin', 'Cameroun')
                product.min_order = opts.get('min_order', 1)
                product.is_active, product.is_featured = True, opts.get('featured', False)
                product.is_flash_deal = opts.get('flash', False)
                product.flash_deal_end = timezone.now() + timedelta(days=rng.randint(2, 7)) if product.is_flash_deal else None
                if is_new:
                    product.views_count = rng.randint(80, 2500)
                    product.orders_count = rng.randint(3, 400)
                for field, path in zip(['image', 'image_2', 'image_3', 'image_4'], files + [None] * 4):
                    setattr(product, field, str(path.relative_to(settings.MEDIA_ROOT)).replace('\\', '/') if path else None)
                product.save()
                if is_new:
                    warehouse = store.warehouses.filter(is_default=True).first() or store.warehouses.first()
                    product.adjust_stock(stock, 'in', reason='Stock initial (catalogue de démonstration)', warehouse=warehouse)
                    for i in range(rng.randint(1, 3) if opts.get('featured') or opts.get('flash') else rng.randint(0, 2)):
                        rating, comment = rng.choice(COMMENTS)
                        Review.objects.create(product=product, name=rng.choice(REVIEWERS), rating=rating, comment=comment)
                    created += 1
                else:
                    updated += 1

        archived = 0
        if not keep_old:
            archived = (Product.objects.filter(store__in=stores.values(), is_active=True)
                        .exclude(sku__startswith=SKU_PREFIX).filter(Q(image='') | Q(image__isnull=True)).update(is_active=False))
        self.stdout.write(self.style.SUCCESS(
            f'{created} produit(s) créé(s), {updated} mis à jour, {skipped} ignoré(s), '
            f'{archived} ancien(s) produit(s) sans photo archivé(s).'))

    @staticmethod
    def _json(url):
        with urlopen(Request(url, headers={'User-Agent': 'Comptoir-demo'}), timeout=30) as r:
            return json.loads(r.read().decode('utf-8'))

    def _download(self, url, path):
        if path.exists() and path.stat().st_size > 0:
            return path
        try:
            with urlopen(Request(url, headers={'User-Agent': 'Comptoir-demo'}), timeout=30) as r:
                path.write_bytes(r.read())
            return path
        except OSError as exc:
            self.stdout.write(self.style.WARNING(f'Photo non téléchargée ({url}) : {exc}'))
            return None
