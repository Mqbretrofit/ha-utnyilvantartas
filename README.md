# Útnyilvántartás – Home Assistant

Home Assistant egyéni integráció **Alapnyomkövetés + Kelio** adatokból készített havi útnyilvántartáshoz.

Aktuális verzió: **0.4.43**

[![Open your Home Assistant instance and open this repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Mqbretrofit&repository=ha-utnyilvantartas&category=integration)

## Fő funkciók

- Alapnyomkövetés napi és havi GPS-adatok feldolgozása.
- Kelio jelenléti napok figyelembevétele.
- Reggeli és esti bejárási jogosultság külön értékelése.
- Saját autós bejárási kilométer és térítés számítása.
- Hónapról hónapra folytonos óraállás.
- Kézi hónapvégi kiegészítés, ha az utolsó munkanapi Kelio-adat még nem érhető el.
- A kézi kiegészítés automatikus felülírása, amikor a valódi Kelio-adat megérkezik.
- Alapnyomkövetés szerveroldali útvonal-kiértékelés megjelenítése.
- GPX export és napi útvonal megjelenítés.
- Nyomtatható havi PDF, AXEL-jellegű elrendezéssel.
- NAV havi üzemanyagár és NAV fogyasztási norma támogatás.
- Havi PDF küldése e-mailben Home Assistant SMTP integráción keresztül.
- Mentett PDF-ek listázása, előnézete, megnyitása és nyomtatása.
- Saját oldalsávos JavaScript dashboard.

## Telepítés

Másold a repository `custom_components/utnyilvantartas` könyvtárát ide:

```text
/config/custom_components/utnyilvantartas
```

Ezután indítsd újra a Home Assistantot, majd:

**Beállítások → Eszközök és szolgáltatások → Integráció hozzáadása → Útnyilvántartás**

Az első beállításnál szükséges:

- Alapnyomkövetés felhasználónév és jelszó;
- GPS-eszköz azonosító;
- otthon és munkahely Home Assistant zóna;
- Kelio napi és havi jelenléti entitások.

A személyes PDF-adatok, címek, rendszám, óraállás és e-mail cím az integráció **Beállítások** oldalán adhatók meg. A repository nem tartalmaz felhasználóspecifikus adatokat vagy hitelesítő adatokat.

## Dashboard

Az integráció saját oldalsávos panelt regisztrál **Útnyilvántartás** néven. Itt látható többek között:

- havi összesítés;
- napi JÁR / NEM JÁR döntés;
- részletes GPS- és bejárási diagnosztika;
- Alapnyomkövetés Kiértékelés táblázat;
- PDF-generálás és e-mail küldés;
- kézi hónapvégi kiegészítés;
- mentett PDF-ek archívuma.

## PDF-ek

A generált havi PDF-ek alapértelmezett helye:

```text
/config/www/utnyilvantartas/
```

A dashboardból megnyithatók és nyomtathatók. Az e-mailes csatoláshoz a rendszer a Home Assistant Media Source könyvtárába készít másolatot.

## E-mail küldés

Az e-mail funkcióhoz előbb állítsd be a Home Assistant **SMTP** integrációját. Az Útnyilvántartás beállításaiban megadható:

- címzett;
- tárgy;
- levélszöveg.

Használható változók: `{month}`, `{employee}`, `{company}`, `{filename}`.

## 0.4.43

- Az előző release-ben hibásan kódolt brand képek helyett valódi, ellenőrzött PNG került az integrációba.
- A `brand/icon.png` és `brand/logo.png` most szabványos, megnyitható 256×256 PNG.
- A hibás dark/@2x fájlok törölve; a Home Assistant saját fallback mechanizmusa használja a valid ikont.

## 0.4.42

- Teljes helyi brand-készlet az integrációban.
- Normál, dark és @2x ikon/logó variánsok.

## 0.4.40

- Mentett PDF-ek listázása, előnézete, új lapon megnyitása és nyomtatása.
- Kézi kiegészítés elrendezési javításai.
- Hosszú PDF-szövegek automatikus méretezése/rövidítése.
- Többoldalas PDF-eknél biztonságos oldaltörés, hogy a táblázat ne fusson a láblécbe.
- A dashboard interaktív elemeinek stabilizálása a Home Assistant gyakori state-frissítései mellett.

## Megjegyzés

Ez egy egyéni Home Assistant integráció, nem hivatalos Alapnyomkövetés-, Kelio- vagy Home Assistant-komponens.
