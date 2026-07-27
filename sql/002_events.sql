-- Holtág-történeti, VÍZÁLLÁS SZEMPONTJÁBÓL releváns események (Grafana annotációkhoz).
-- Forrás: a user "A faddi Duna-holtág vízügyi és történelmi eseményei" táblázata (szűkítve).
-- impact: pozitiv = vízszintet/vízháztartást javító; negativ = rontó vagy káresemény.
-- Újrafuttatható: teljes újratöltés.

CREATE TABLE IF NOT EXISTS holtag_events (
  id             BIGSERIAL PRIMARY KEY,
  start_date     DATE NOT NULL,
  end_date       DATE,                 -- NULL = pont-esemény; kitöltve = időszak
  date_precision TEXT NOT NULL,        -- nap | honap | evszak | ev | idoszak
  title          TEXT NOT NULL,
  location       TEXT,
  event_type     TEXT NOT NULL,
  impact         TEXT NOT NULL CHECK (impact IN ('pozitiv', 'negativ')),
  sources        TEXT
);

TRUNCATE holtag_events;

INSERT INTO holtag_events (start_date, end_date, date_precision, title, location, event_type, impact, sources) VALUES
-- Negatív: a holtág vízutánpótlását elvágó szabályozások, ár- és káresemények
('1838-01-01', NULL, 'ev', 'Borrévi jégtorlasz okozta jeges árvíz; 7 km-es kanyárátvágás, a Fadd-Dombori-holtág létrejötte', 'Tolnai kanyarulat / Borrév', 'természeti esemény (jeges árvíz)', 'negativ', '3, 2'),
('1841-01-01', '1846-12-31', 'idoszak', 'A faddi Duna-kanyar átvágása — a holtág elvágásának kezdete', 'Fadd-Dombori szakasz', 'szabályozás', 'negativ', 'Inferred'),
('1850-01-01', NULL, 'ev', 'Bogyiszlói átvágás; a holtmeder végleges lefűződése a Dunáról', 'Faddi-Holt-Duna', 'szabályozás', 'negativ', '1, 4'),
('1856-01-01', NULL, 'ev', 'Az átvágás válik a Duna fő medrévé; a tolnai szakasz holtággá válik', 'Tolna', 'természeti esemény', 'negativ', '2'),
('1862-01-01', NULL, 'ev', 'Árvíz Tolna városában (a Duna időszakos visszatérése)', 'Tolna', 'természeti esemény', 'negativ', '2'),
('1893-01-01', NULL, 'ev', 'A holtág végleges leválasztása az élő Dunáról', 'Fadd-Dombori Holt-Duna', 'szabályozás', 'negativ', 'Inferred'),
('2025-08-15', NULL, 'honap', 'Dombori II-es strand ideiglenes bezárása vízminőségi probléma miatt', 'Dombori II-es strand', 'természeti esemény', 'negativ', '13'),
-- Pozitív: vízpótlás, kotrás, duzzasztás, vízkormányzás
('1895-01-01', NULL, 'ev', 'A faddi és tolnai holtágat összekötő Bartal-csatorna megépítése (1890-es évek vége)', 'Bartal-csatorna', 'szabályozás', 'pozitiv', '3'),
('1975-01-01', NULL, 'ev', 'Önálló gondnokság; mederrendezés és vízminőség-javítás', 'Fadd-Dombori-holtág', 'rehabilitáció', 'pozitiv', '3'),
('1988-01-01', NULL, 'ev', 'Az 1500 m-es kajak-kenu pálya megépítése, mederkotrással', 'Kajak-kenu pálya', 'rehabilitáció', 'pozitiv', '3'),
('1994-01-01', NULL, 'ev', 'Vízpótlás: az atomerőmű hűtővizének bevezetése a holtágba (Paks-faddi főcsatorna / Csámpai-patak)', 'Fadd-Dombori térsége', 'rehabilitáció', 'pozitiv', '3, 2, 8'),
('2013-01-01', NULL, 'ev', 'Mederkotrás a kajakpályán (1 km hosszan, 100 m szélesen)', 'Kajak-kenu pálya', 'rehabilitáció', 'pozitiv', '3'),
('2014-01-01', '2015-12-31', 'idoszak', 'Komplex élőhely-rehabilitáció és a vízpótló rendszer korszerűsítése', 'Teljes holtág', 'rehabilitáció', 'pozitiv', 'Inferred'),
('2021-02-01', NULL, 'honap', 'A holtág és a Bartal-csatorna kotrásának megkezdése', 'Bartal-csatorna', 'rehabilitáció', 'pozitiv', '3'),
('2021-07-29', NULL, 'nap', 'Rendeződött a vízszint (450 cm) — ideális kajakos állapot', 'Tolnai holtág', 'rehabilitáció', 'pozitiv', '3'),
('2022-06-21', NULL, 'nap', 'Történelmi zsilipnyitás: először engedtek át vizet a tolnai holtágból Domboriba az aszály miatt', 'Bartal-zsilip', 'rehabilitáció', 'pozitiv', '3'),
('2022-10-15', NULL, 'evszak', 'Vízutánpótlás a Tolnai-holtágon át a Sióból és a Dunából; csapadékos ősz', 'Faddi Duna-holtág', 'rehabilitáció / természeti esemény', 'pozitiv', '10'),
('2024-10-15', NULL, 'evszak', 'Gravitációs vízpótlási ciklus a Sió felől; 1,5 millió m3 víz', 'Tolnai-Holt-Duna', 'rehabilitáció', 'pozitiv', '1, 11'),
('2025-05-15', '2025-06-30', 'idoszak', 'Duzzasztás a Sió-árvízkapu lezárásával + teljes vízpótlási ciklus a Kutyatanyai-zsilipen át (kb. 1 117 600 m3)', 'Sió-csatorna / Tolnai-Holt-Duna', 'szabályozás / rehabilitáció', 'pozitiv', '1, 12, 11'),
('2025-08-04', NULL, 'nap', 'Nyári rendkívüli, ökológiai célú vízpótlás az alpesi árhullám tetőzésekor (Paks-Faddi főcsatorna)', 'Tolnai, Faddi és Bogyiszlói holtág', 'rehabilitáció', 'pozitiv', '1, 11'),
('2026-03-15', NULL, 'honap', 'Tavaszi dunai árhullámot hasznosító gravitációs feltöltés (Kutyatanyai-, Karaszifoki- és Dombori-szivornya)', 'Tolnai, Faddi és Bogyiszlói holtág', 'rehabilitáció', 'pozitiv', '1, 14'),
('2026-06-08', NULL, 'nap', 'Komplex partfal-rekonstrukció és turisztikai fejlesztés megkezdése', 'Fadd-Dombori', 'rehabilitáció', 'pozitiv', '1');
