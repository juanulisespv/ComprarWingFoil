#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import csv
import sys
import json
import time
import random
import logging
import argparse
from urllib.parse import urlparse

# Selenium Imports
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('scraper.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# Try to import undetected_chromedriver
try:
    import undetected_chromedriver as uc
    HAS_UC = True
except ImportError:
    HAS_UC = False
    logger.warning("undetected-chromedriver no está instalado o no se pudo importar. Usando Selenium estándar.")

class WallapopScraper:
    def __init__(self, use_uc=True, headless=False, timeout=15):
        self.use_uc = use_uc and HAS_UC
        self.headless = headless
        self.timeout = timeout
        self.driver = None

    def init_driver(self):
        """Inicializa el driver de Selenium (o undetected-chromedriver)"""
        logger.info(f"Inicializando WebDriver (use_uc={self.use_uc}, headless={self.headless})...")
        
        if self.use_uc:
            try:
                options = uc.ChromeOptions()
                if self.headless:
                    options.add_argument('--headless')
                # Desactivar sandbox para entornos Linux/Docker si fuera necesario
                options.add_argument('--no-sandbox')
                options.add_argument('--disable-dev-shm-usage')
                options.add_argument('--disable-gpu')
                options.add_argument('--window-size=1920,1080')
                
                # Iniciar uc.Chrome
                self.driver = uc.Chrome(options=options)
                logger.info("WebDriver de undetected-chromedriver iniciado con éxito.")
                return
            except Exception as e:
                logger.error(f"Fallo al iniciar undetected-chromedriver: {e}. Reintentando con Selenium estándar...")
                self.use_uc = False

        # Fallback to standard Selenium Chrome
        options = webdriver.ChromeOptions()
        if self.headless:
            options.add_argument('--headless')
        
        # Evadir firmas básicas de detección en Selenium estándar
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        options.add_argument('--window-size=1920,1080')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        
        # User-Agent realista
        options.add_argument("user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        
        self.driver = webdriver.Chrome(options=options)
        
        # Ejecutar script para eliminar el flag webdriver del navegador
        self.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "const newProto = navigator.__proto__; delete newProto.webdriver; navigator.__proto__ = newProto;"
        })
        logger.info("WebDriver de Selenium estándar iniciado con éxito.")

    def close(self):
        """Cierra el navegador"""
        if self.driver:
            logger.info("Cerrando WebDriver...")
            try:
                self.driver.quit()
            except Exception as e:
                logger.error(f"Error al cerrar WebDriver: {e}")

    def check_for_blocks(self):
        """Verifica si la página actual presenta bloqueos de Cloudflare o errores 403"""
        if not self.driver:
            return False
            
        title = self.driver.title.lower()
        blocking_titles = [
            "cloudflare", "attention required", "just a moment", 
            "security check", "ddos protection", "captcha", "bot detection",
            "access denied", "forbidden", "bloqueo"
        ]
        
        for bt in blocking_titles:
            if bt in title:
                logger.error(f"Bloqueo detectado en el título de la página: '{self.driver.title}'")
                return True
                
        try:
            body_text = self.driver.find_element(By.TAG_NAME, "body").text.lower()
            blocking_phrases = [
                "403 forbidden", "access denied", "ip blocked", 
                "checking your browser", "unusual traffic", "captcha"
            ]
            for bp in blocking_phrases:
                if bp in body_text[:1000]: # revisar los primeros caracteres para eficiencia
                    logger.error(f"Bloqueo detectado en el texto del cuerpo de la página.")
                    return True
        except Exception:
            pass
            
        return False

    def accept_cookies(self):
        """Intenta hacer clic en el botón de aceptar cookies si aparece (incluyendo Shadow DOM)"""
        # 1. Intentar el método JS para ConsentManager (Shadow DOM), que es el que usa Wallapop
        try:
            cmp_wrapper = self.driver.find_elements(By.ID, "cmpwrapper")
            if cmp_wrapper:
                logger.info("Detectado el contenedor de cookies de ConsentManager (#cmpwrapper). Intentando cerrar vía Shadow DOM...")
                self.driver.execute_script(
                    "document.querySelector('#cmpwrapper').shadowRoot.querySelector('.cmpboxbtnyes').click();"
                )
                logger.info("Cookies aceptadas con éxito en el Shadow DOM de ConsentManager.")
                time.sleep(1.5)
                return
        except Exception as e:
            logger.debug(f"No se pudo hacer clic en el botón de ConsentManager en Shadow DOM: {e}")

        # 2. Métodos fallback tradicionales en el DOM principal
        xpaths = [
            "//button[@id='onetrust-accept-btn-handler']",
            "//button[@id='didomi-notice-agree-button']",
            "//button[contains(translate(text(), 'ACEPTAR', 'aceptar'), 'aceptar')]",
            "//button[contains(translate(text(), 'AGREE', 'agree'), 'agree')]",
            "//button[contains(translate(text(), 'ACEPTO', 'acepto'), 'acepto')]",
            "//button[contains(@class, 'accept') or contains(@class, 'agree')]"
        ]
        
        for xpath in xpaths:
            try:
                wait = WebDriverWait(self.driver, 2)
                btn = wait.until(EC.element_to_be_clickable((By.XPATH, xpath)))
                btn.click()
                logger.info(f"Cookies aceptadas con éxito usando el selector: '{xpath}'")
                time.sleep(1.5)
                return
            except Exception:
                continue
        logger.info("No se detectó el banner de cookies o ya se aceptó previamente.")

    def scrape_search_results(self, search_url, max_ads=100, ignore_separator=False):
        """
        Abre la URL de búsqueda, realiza scroll gradual y recopila las URLs de los anuncios.
        """
        logger.info(f"Navegando a la URL de búsqueda: {search_url}")
        self.driver.get(search_url)
        
        if self.check_for_blocks():
            raise RuntimeError("Bloqueo detectado al cargar la página de búsqueda.")
            
        self.accept_cookies()
        
        collected_urls = set()
        
        limit_desc = f"{max_ads} anuncios" if max_ads > 0 else "todos los anuncios posibles"
        logger.info(f"Iniciando scroll gradual para recopilar {limit_desc}...")
        
        # Obtener altura inicial de la página
        last_height = self.driver.execute_script("return document.body.scrollHeight")
        no_growth_attempts = 0
        max_no_growth_attempts = 4  # reintentos si la página no crece
        
        separator_found_in_session = False
        
        while max_ads == 0 or len(collected_urls) < max_ads:
            # 1. Comprobar si ha aparecido el separador de resultados recomendados ("También te puede interesar" o "Relacionados")
            separator_y = None
            if not ignore_separator:
                try:
                    separators = self.driver.find_elements(
                        By.XPATH, 
                        "//*[contains(text(), 'También te puede interesar') or contains(text(), 'también te puede interesar') or contains(text(), 'relacionados') or contains(text(), 'Relacionados')]"
                    )
                    for sep in separators:
                        if sep.is_displayed():
                            # Obtener coordenada Y del separador
                            separator_y = sep.location['y']
                            logger.info(f"Detectada sección de recomendaciones o anuncios irrelevantes ('{sep.text}') en Y={separator_y}. Deteniendo scroll y acotando resultados.")
                            separator_found_in_session = True
                            break
                except Exception:
                    pass

            # Obtener posición actual del scroll
            current_position = self.driver.execute_script("return window.pageYOffset;")
            target_position = last_height
            
            # Scroll gradual en pasos pequeños/medianos aleatorios para simular lectura humana
            # y forzar la activación de triggers de lazy loading en la página
            logger.info("Realizando scroll gradual hacia el fondo de la página...")
            while current_position < target_position:
                step = random.randint(300, 600)
                current_position += step
                if current_position > target_position:
                    current_position = target_position
                self.driver.execute_script(f"window.scrollTo(0, {current_position});")
                time.sleep(random.uniform(0.1, 0.25))
            
            # Esperar aleatoriamente a que carguen los nuevos anuncios (2.5 a 4.0s)
            wait_time = random.uniform(2.5, 4.0)
            logger.info(f"Llegado al final temporal de la página. Esperando {wait_time:.2f}s para cargar más resultados...")
            time.sleep(wait_time)
            
            # Buscar y pulsar botones de "Cargar más" (para cuando se detiene el scroll automático)
            try:
                # 1. Intentar hacer clic en el componente personalizado <walla-button> (que usa Shadow DOM)
                clicked_walla = self.driver.execute_script("""
                    const wallaBtns = document.querySelectorAll("walla-button");
                    for (const btn of wallaBtns) {
                        const attrText = (btn.getAttribute("text") || "").toLowerCase();
                        const innerTxt = (btn.innerText || "").toLowerCase();
                        if (attrText.includes("cargar") || attrText.includes("ver") || 
                            innerTxt.includes("cargar") || innerTxt.includes("ver")) {
                            
                            // Asegurarse de que el elemento sea visible y tenga dimensiones reales
                            const rect = btn.getBoundingClientRect();
                            if (rect.width > 0 && rect.height > 0) {
                                btn.scrollIntoView({block: 'center'});
                                if (btn.shadowRoot) {
                                    const innerBtn = btn.shadowRoot.querySelector("button");
                                    if (innerBtn) {
                                        innerBtn.click();
                                        return true;
                                    }
                                }
                                btn.click();
                                return true;
                            }
                        }
                    }
                    return false;
                """)
                
                if clicked_walla:
                    logger.info("¡Pulsado el botón personalizado <walla-button> de 'Cargar más' (Shadow DOM) de forma automática!")
                    time.sleep(3)
                    no_growth_attempts = 0
                else:
                    # 2. Fallback: Buscar en todos los elementos clicables comunes del DOM principal (si no se usa walla-button)
                    potential_buttons = self.driver.find_elements(By.XPATH, "//*[self::button or self::a or self::div or self::span]")
                    for btn in potential_buttons:
                        try:
                            if btn.is_displayed():
                                txt = btn.text.strip().lower()
                                if "cargar" in txt or "ver más" in txt or "ver mas" in txt or "mostrar" in txt:
                                    # Comprobar tamaño para no pulsar un contenedor gigante por error
                                    size = btn.size
                                    if 0 < size['width'] < 500 and 0 < size['height'] < 100:
                                        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                                        time.sleep(1)
                                        btn.click()
                                        logger.info(f"¡Pulsado el botón de 'Cargar más' resultados de forma automática! Tag: '{btn.tag_name}', Texto: '{btn.text}'")
                                        time.sleep(3)
                                        no_growth_attempts = 0  # resetear ya que cargamos más
                                        break
                        except Exception:
                            continue
            except Exception as e:
                logger.debug(f"No se pudo hacer clic en el botón de Cargar más: {e}")

            # Recopilar URLs actuales que contengan /item/
            try:
                elements = self.driver.find_elements(By.XPATH, "//a[contains(@href, '/item/')]")
                new_urls_count = 0
                for el in elements:
                    try:
                        # Si está por debajo del separador, omitirlo
                        if separator_y is not None:
                            try:
                                el_y = el.location['y']
                                if el_y >= separator_y:
                                    continue
                            except Exception:
                                pass

                        href = el.get_attribute("href")
                        if href and "/item/" in href:
                            # Limpiar parámetros de consulta
                            clean_url = href.split('?')[0]
                            if not clean_url.startswith("http"):
                                clean_url = "https://es.wallapop.com" + clean_url
                            
                            if clean_url not in collected_urls:
                                collected_urls.add(clean_url)
                                new_urls_count += 1
                    except Exception:
                        continue
                logger.info(f"Progreso: {len(collected_urls)} anuncios recopilados en total.")
            except Exception as e:
                logger.warning(f"Error al buscar elementos de anuncio: {e}")

            if separator_found_in_session:
                logger.info("Deteniendo scroll de búsqueda porque ya se ha alcanzado la sección de recomendaciones.")
                break

            if max_ads > 0 and len(collected_urls) >= max_ads:
                break
                
            # Verificar si la altura de la página ha crecido
            new_height = self.driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                no_growth_attempts += 1
                logger.info(f"La página no ha crecido (intento {no_growth_attempts}/{max_no_growth_attempts}).")
                if no_growth_attempts >= max_no_growth_attempts:
                    logger.info("Se ha alcanzado el final real de los anuncios en Wallapop.")
                    break
            else:
                no_growth_attempts = 0
                last_height = new_height
                
            # Verificar bloqueos
            if self.check_for_blocks():
                logger.error("Se detectó un bloqueo durante el scroll de búsqueda.")
                break
                
        # Retornar lista limitada al máximo configurado
        return list(collected_urls)[:max_ads] if max_ads > 0 else list(collected_urls)


class DataExtractor:
    @staticmethod
    def extract_liters(text: str) -> list:
        """
        Busca patrones de litros en el texto (ej. '140L', '145 litros', '140l')
        Valores aceptables en el rango de 30 a 250 litros.
        """
        if not text:
            return []
        
        # Patrón principal: busca números seguidos de l, litros, lts, ltrs, lt, litri
        pattern = r'\b(\d+(?:[.,]\d+)?)\s*(?:l|litros?|lts|ltrs|lt|litri)\b'
        matches = re.findall(pattern, text, re.IGNORECASE)
        
        results = []
        for m in matches:
            val_str = m.replace(',', '.')
            try:
                val = float(val_str)
                # Filtrar litros razonables para tablas de wingfoil (30L a 250L)
                if 30 <= val <= 250:
                    results.append(int(val) if val.is_integer() else val)
            except ValueError:
                continue
                
        return sorted(list(set(results)))

    @staticmethod
    def extract_wing_size(text: str) -> list:
        """
        Busca patrones de tamaño de ala (ej. '6m', '6.0m²', 'ala de 6')
        Valores aceptables en el rango de 2.0 a 10.0 metros.
        """
        if not text:
            return []
            
        # Patrón 1: número seguido de m, metros, meters, m2, m²
        pattern1 = r'\b(\d+(?:[.,]\d+)?)\s*(?:m|m²|m2|meters?|metros?)\b'
        # Patrón 2: palabras clave de ala seguidas de número (ej. 'ala de 6', 'alas de 5', 'ala 5')
        pattern2 = r'\balas?\s*(?:de\s*)?(\d+(?:[.,]\d+)?)(?![.,]\d)\b'
        
        matches = []
        for m in re.findall(pattern1, text, re.IGNORECASE):
            matches.append(m)
        for m in re.findall(pattern2, text, re.IGNORECASE):
            matches.append(m)
            
        results = []
        for m in matches:
            val_str = m.replace(',', '.')
            try:
                val = float(val_str)
                # Filtrar tamaños razonables de ala (2.0m a 10.0m)
                if 2.0 <= val <= 10.0:
                    results.append(int(val) if val.is_integer() else val)
            except ValueError:
                continue
                
        return sorted(list(set(results)))

    @staticmethod
    def find_item_in_dict(d):
        """Busca de forma recursiva un diccionario que represente al anuncio"""
        if not isinstance(d, dict):
            return None
            
        # Un item de Wallapop suele tener por lo menos 'title', 'price' y 'description'
        if 'title' in d and 'price' in d and ('description' in d or 'location' in d):
            return d
            
        for key, val in d.items():
            if isinstance(val, dict):
                res = DataExtractor.find_item_in_dict(val)
                if res:
                    return res
            elif isinstance(val, list):
                for item in val:
                    if isinstance(item, dict):
                        res = DataExtractor.find_item_in_dict(item)
                        if res:
                            return res
        return None

    @classmethod
    def parse_listing_page(cls, driver, url):
        """
        Extrae la información de un anuncio usando __NEXT_DATA__
        o selectores HTML alternativos en caso de fallo.
        """
        title = ""
        price = ""
        location = ""
        description = ""
        date = ""
        envio = ""
        estado = "Disponible"
        imagen = ""

        # Intentar extraer mediante __NEXT_DATA__ (JSON estructurado de Next.js)
        try:
            wait = WebDriverWait(driver, 10)
            script_el = wait.until(EC.presence_of_element_located((By.ID, "__NEXT_DATA__")))
            json_text = script_el.get_attribute("textContent")
            data = json.loads(json_text)
            
            # Buscar el objeto item dentro del JSON
            item_data = cls.find_item_in_dict(data)
            if item_data:
                # Titulo
                title_obj = item_data.get("title", "")
                if isinstance(title_obj, dict):
                    title = title_obj.get("original", "") or title_obj.get("translated", "") or ""
                else:
                    title = str(title_obj)
                title = title.strip()
                
                # Descripcion
                desc_obj = item_data.get("description", "")
                if isinstance(desc_obj, dict):
                    description = desc_obj.get("original", "") or desc_obj.get("translated", "") or ""
                else:
                    description = str(desc_obj)
                description = description.strip()
                
                # Procesar precio
                price_obj = item_data.get("price")
                if isinstance(price_obj, dict):
                    cash_obj = price_obj.get("cash", {})
                    if isinstance(cash_obj, dict):
                        price = cash_obj.get("amount")
                    if price is None:
                        price = price_obj.get("amount") or price_obj.get("value")
                else:
                    price = price_obj if price_obj is not None else ""
                
                # Procesar ubicación
                loc_obj = item_data.get("location", {})
                if isinstance(loc_obj, dict):
                    city = loc_obj.get("city", "")
                    region = loc_obj.get("region", "")
                    postal_code = loc_obj.get("postalCode", "")
                    location = ", ".join(filter(None, [city, region, postal_code])).strip()
                else:
                    location = str(loc_obj).strip()
                
                # Procesar fecha de publicación (modifiedDate o creationDate)
                date_val = item_data.get("modifiedDate") or item_data.get("creationDate")
                if date_val:
                    if isinstance(date_val, (int, float)):
                        try:
                            # Convertir milisegundos a formato YYYY-MM-DD
                            date = time.strftime('%Y-%m-%d', time.localtime(date_val / 1000.0))
                        except Exception:
                            date = str(date_val)
                    else:
                        date = str(date_val)
                
                if not date:
                    date = item_data.get("publishedDate") or item_data.get("creationDate") or item_data.get("published_date") or ""
                
                if isinstance(date, str) and date:
                    date = date.split('T')[0]
                
                # Procesar envío
                shipping_obj = item_data.get("shipping", {})
                if isinstance(shipping_obj, dict):
                    is_shippable = shipping_obj.get("isItemShippable")
                    if is_shippable is not None:
                        envio = "Envío disponible" if is_shippable else "Envío no disponible"
                
                # Procesar estado (reservado, vendido)
                flags_obj = item_data.get("flags", {})
                if isinstance(flags_obj, dict):
                    if flags_obj.get("sold"):
                        estado = "Vendido"
                    elif flags_obj.get("reserved"):
                        estado = "Reservado"
                        
                # Procesar imagenes (todas las disponibles)
                img_urls = []
                images = item_data.get("images", [])
                if isinstance(images, list):
                    for img in images:
                        if isinstance(img, dict):
                            urls_dict = img.get("urls", {})
                            if isinstance(urls_dict, dict):
                                u = urls_dict.get("medium") or urls_dict.get("small") or urls_dict.get("big") or ""
                                if u:
                                    img_urls.append(u)
                if img_urls:
                    imagen = "|".join(img_urls)
                    
                logger.info("Datos extraídos con éxito mediante __NEXT_DATA__.")
        except Exception as e:
            logger.warning(f"No se pudo extraer mediante __NEXT_DATA__ ({e}). Usando selectores HTML alternativos...")

        # Fallback a selectores HTML / Título de la página / OG Tags
        # El título de la página en Wallapop suele ser:
        # "Titulo del item de segunda mano por X EUR en Ciudad en WALLAPOP"
        page_title = driver.title if driver else ""
        
        if not title:
            if " de segunda mano por " in page_title:
                try:
                    title = page_title.split(" de segunda mano por ")[0].strip()
                except Exception:
                    pass
            elif " de segunda mano en " in page_title:
                try:
                    title = page_title.split(" de segunda mano en ")[0].strip()
                except Exception:
                    pass
            
            if not title:
                try:
                    title_meta = driver.find_element(By.XPATH, "//meta[@property='og:title']")
                    title = title_meta.get_attribute("content").strip()
                except Exception:
                    try:
                        title = driver.find_element(By.TAG_NAME, "h1").text.strip()
                    except Exception:
                        pass

        if not description:
            try:
                desc_meta = driver.find_element(By.XPATH, "//meta[@property='og:description'] | //meta[@name='description']")
                description = desc_meta.get_attribute("content").strip()
            except Exception:
                for selector in [".item-detail-description", ".product-info-description", "div[class*='description']"]:
                    try:
                        description = driver.find_element(By.CSS_SELECTOR, selector).text.strip()
                        if description:
                            break
                    except Exception:
                        continue

        if not price:
            if " por " in page_title and " en " in page_title:
                try:
                    # Extraer precio de: "... por 20 EUR en Ciudad..."
                    parts = page_title.split(" por ")
                    if len(parts) > 1:
                        price_part = parts[1].split(" en ")[0].strip()
                        price_num = re.findall(r'\d+(?:[.,]\d+)?', price_part)
                        if price_num:
                            price = price_num[0].replace(',', '.')
                except Exception:
                    pass
            
            if not price:
                try:
                    price_meta = driver.find_element(By.XPATH, "//meta[@property='product:price:amount']")
                    price = price_meta.get_attribute("content").strip()
                except Exception:
                    for selector in [".item-detail-price", "span[class*='price']", ".price"]:
                        try:
                            price_text = driver.find_element(By.CSS_SELECTOR, selector).text.strip()
                            price_num = re.findall(r'\d+(?:[.,]\d+)?', price_text)
                            if price_num:
                                price = price_num[0].replace(',', '.')
                                break
                        except Exception:
                            continue

        if not location:
            if " en " in page_title and " en WALLAPOP" in page_title:
                try:
                    # Extraer ubicación de: "... en Ciudad en WALLAPOP"
                    parts = page_title.split(" en ")
                    if len(parts) > 2:
                        # el penúltimo elemento suele ser la ciudad
                        location = parts[-2].strip()
                except Exception:
                    pass
            
            if not location:
                for selector in [".item-detail-location", "span[class*='location']", ".location-name"]:
                    try:
                        location = driver.find_element(By.CSS_SELECTOR, selector).text.strip()
                        if location:
                            break
                    except Exception:
                        continue

        if not date:
            try:
                body_text = driver.find_element(By.TAG_NAME, "body").text
                date_match = re.search(r'publicado\s+(?:hace|el)\s+([\w\s\d.-]+)', body_text, re.IGNORECASE)
                if date_match:
                    date = date_match.group(0).strip()
            except Exception:
                pass

        # Fallback para envío si no se obtuvo del JSON
        if not envio:
            try:
                body_text = driver.find_element(By.TAG_NAME, "body").text.lower()
                if "este artículo no admite envíos" in body_text or "no realiza envíos" in body_text:
                    envio = "Envío no disponible"
                elif "envío disponible" in body_text or "se envía" in body_text or "hacer envío" in body_text:
                    envio = "Envío disponible"
            except Exception:
                pass

        if not envio:
            envio = "No especificado"

        # Fallback para imagen
        if not imagen:
            try:
                img_meta = driver.find_elements(By.XPATH, "//meta[@property='og:image']")
                img_urls = [m.get_attribute("content").strip() for m in img_meta if m.get_attribute("content")]
                if img_urls:
                    imagen = "|".join(img_urls)
            except Exception:
                try:
                    img_els = driver.find_elements(By.XPATH, "//div[contains(@class, 'carousel')]//img | //img[contains(@src, 'cdn.wallapop.com/images')]")
                    img_urls = [el.get_attribute("src").strip() for el in img_els if el.get_attribute("src")]
                    if img_urls:
                        imagen = "|".join(img_urls)
                except Exception:
                    pass

        # Fallback para estado si no se obtuvo del JSON
        if estado == "Disponible":
            try:
                page_title_lower = page_title.lower()
                if "reservado" in page_title_lower:
                    estado = "Reservado"
                elif "vendido" in page_title_lower:
                    estado = "Vendido"
                else:
                    badges = driver.find_elements(By.XPATH, "//*[text()='Reservado' or text()='Vendido' or text()='reservado' or text()='vendido']")
                    for b in badges:
                        if b.is_displayed():
                            txt = b.text.strip().lower()
                            if txt == "reservado":
                                estado = "Reservado"
                                break
                            elif txt == "vendido":
                                estado = "Vendido"
                                break
            except Exception:
                pass

        # Limpiar precio a float si es string numérico
        if price:
            try:
                price = float(str(price).replace('€', '').replace(' ', '').replace(',', '.').strip())
            except ValueError:
                pass

        return {
            "titulo": title,
            "precio": price,
            "ubicacion": location,
            "descripcion": description,
            "url": url,
            "fecha_publicacion": date,
            "envio": envio,
            "estado": estado,
            "imagen": imagen
        }


def run_tests():
    """Ejecuta pruebas unitarias sencillas sobre las expresiones regulares de extracción"""
    logger.info("Ejecutando pruebas sobre expresiones regulares...")
    
    # Test casos para litros
    test_cases_liters = [
        ("Tabla Fanatic Sky Wing de 140 litros", [140]),
        ("Vendo tabla wingfoil 140L y ala de 6m", [140]),
        ("Tabla de 95 litros en perfecto estado", [95]),
        ("Volumen: 145 lts de capacidad", [145]),
        ("Tabla de 140,5 l", [140.5]),
        ("Tavola Wingfoil Air Beluga 155 90lt", [90]),
        ("Volume: 90 litri", [90]),
        ("Tabla chica 35l, y tabla grande 150 litros", [35, 150]),
        ("No tiene litros especificados, 1000 litros no es válido", []), # Fuera de rango
        ("Precio 140 liras", []), # Falsa coincidencia
    ]
    
    for text, expected in test_cases_liters:
        result = DataExtractor.extract_liters(text)
        assert result == expected, f"Fallo litros. Texto: '{text}'. Esperado: {expected}, Obtenido: {result}"
        
    # Test casos para alas
    test_cases_wings = [
        ("Vendo ala de 6 metros Duotone", [6]),
        ("Ala 6.0m² en buen estado", [6]),
        ("Compro ala de 5.5m", [5.5]),
        ("Kit completo tabla 140l y ala 6", [6]),
        ("Ala 4.2 y ala 5m²", [4.2, 5]),
        ("Ala de 5,8 metros", [5.8]),
        ("Con las alas de 4.0 , 5.0 y 6.0 metros", [4, 6]),
        ("Ala de 12 metros", []), # Fuera de rango
    ]
    
    for text, expected in test_cases_wings:
        result = DataExtractor.extract_wing_size(text)
        assert result == expected, f"Fallo alas. Texto: '{text}'. Esperado: {expected}, Obtenido: {result}"
        
    logger.info("¡Todas las pruebas de Regex pasaron correctamente!")


def main():
    parser = argparse.ArgumentParser(description="Wallapop Wingfoil Scraper con Selenium")
    parser.add_argument("--url", type=str, default="https://es.wallapop.com/search?keywords=wingfoil&order_by=most_relevance",
                        help="URL de búsqueda de Wallapop")
    parser.add_argument("--limit", type=int, default=100,
                        help="Máximo de anuncios a recopilar y procesar (defecto 100)")
    parser.add_argument("--output", type=str, default="resultados_wallapop.csv",
                        help="Archivo CSV de salida")
    parser.add_argument("--failed-log", type=str, default="failed_listings.log",
                        help="Archivo de log para anuncios que fallaron")
    parser.add_argument("--headless", action="store_true",
                        help="Ejecutar el navegador en modo headless")
    parser.add_argument("--no-uc", action="store_true",
                        help="Desactivar undetected-chromedriver y usar Selenium estándar")
    parser.add_argument("--min-liters", type=float, default=140.0,
                        help="Litros mínimos para cumplir criterios (defecto > 140)")
    parser.add_argument("--min-wing", type=float, default=5.8,
                        help="Tamaño mínimo de ala para cumplir criterios (defecto 5.8)")
    parser.add_argument("--max-wing", type=float, default=6.2,
                        help="Tamaño máximo de ala para cumplir criterios (defecto 6.2)")
    parser.add_argument("--run-tests", action="store_true",
                        help="Ejecutar pruebas unitarias internas de regex y salir")
    parser.add_argument("--manual-scroll", "-m", action="store_true",
                        help="Habilitar scroll manual. El script abrirá el buscador y esperará a que presiones ENTER en la terminal para procesar los anuncios cargados.")
    parser.add_argument("--force-update", "-f", action="store_true",
                        help="Forzar la recarga y actualización de todos los anuncios encontrados, incluso si ya existen en el CSV.")
    parser.add_argument("--ignore-separator", action="store_true",
                        help="Ignorar el separador de recomendaciones para no detener la búsqueda ni filtrar anuncios inferiores.")
                        
    args = parser.parse_args()

    # Ejecutar tests si se solicita
    if args.run_tests:
        run_tests()
        sys.exit(0)

    logger.info("=== INICIANDO SCRAPER DE WALLAPOP ===")
    
    scraper = WallapopScraper(use_uc=not args.no_uc, headless=args.headless)
    
    try:
        scraper.init_driver()
        
        # 1. Obtener lista de URLs
        if args.manual_scroll:
            logger.info(f"Navegando a la URL de búsqueda: {args.url}")
            scraper.driver.get(args.url)
            scraper.accept_cookies()
            logger.info("")
            logger.info("==========================================================================")
            logger.info("                 MODO DE SCROLL MANUAL ACTIVO")
            logger.info("==========================================================================")
            logger.info("Por favor, haz scroll en la ventana del navegador y carga todos los anuncios")
            logger.info("que desees procesar (puedes bajar rápido, pulsar Cargar más, etc.).")
            logger.info("Una vez que hayas terminado de cargar los resultados:")
            logger.info(">>> PRESIONA ENTER EN ESTA TERMINAL PARA CONTINUAR Y EXTRAER LOS DATOS <<<")
            logger.info("==========================================================================")
            logger.info("")
            input("Presiona ENTER para continuar...")
            
            # Recopilar URLs cargadas en el DOM
            logger.info("Recopilando enlaces cargados en la página...")
            collected_set = set()
            try:
                elements = scraper.driver.find_elements(By.XPATH, "//a[contains(@href, '/item/')]")
                # Buscar si hay separador de recomendaciones para omitir basura inferior
                separator_y = None
                if not args.ignore_separator:
                    try:
                        separators = scraper.driver.find_elements(
                            By.XPATH, 
                            "//*[contains(text(), 'También te puede interesar') or contains(text(), 'también te puede interesar') or contains(text(), 'relacionados') or contains(text(), 'Relacionados')]"
                        )
                        for sep in separators:
                            if sep.is_displayed():
                                separator_y = sep.location['y']
                                logger.info(f"Separador de recomendaciones detectado en Y={separator_y}. Filtrando anuncios inferiores.")
                                break
                    except Exception:
                        pass

                for el in elements:
                    try:
                        if separator_y is not None:
                            try:
                                el_y = el.location['y']
                                if el_y >= separator_y:
                                    continue
                            except Exception:
                                pass
                        href = el.get_attribute("href")
                        if href and "/item/" in href:
                            clean_url = href.split('?')[0]
                            if not clean_url.startswith("http"):
                                clean_url = "https://es.wallapop.com" + clean_url
                            collected_set.add(clean_url)
                    except Exception:
                        continue
            except Exception as e:
                logger.error(f"Error al recopilar URLs del DOM: {e}")
            urls = list(collected_set)
        else:
            urls = scraper.scrape_search_results(args.url, max_ads=args.limit, ignore_separator=args.ignore_separator)
            
        logger.info(f"Se obtuvieron un total de {len(urls)} URLs para procesar.")
        
        if not urls:
            logger.warning("No se encontraron URLs de anuncios. Terminando proceso.")
            return

        # 2. Inicializar CSV y cargar anuncios ya procesados
        existing_ads = {}
        if os.path.exists(args.output):
            try:
                with open(args.output, 'r', encoding='utf-8') as csvfile:
                    reader = csv.DictReader(csvfile, delimiter=';')
                    for row in reader:
                        url_val = row.get("url")
                        if url_val:
                            existing_ads[url_val.strip()] = row
                logger.info(f"Cargados {len(existing_ads)} anuncios previos de '{args.output}'. Se omitirán los ya procesados.")
            except Exception as e:
                logger.warning(f"No se pudieron leer los anuncios previos de '{args.output}': {e}. Se procesará todo.")

        csv_columns = [
            "titulo", "precio", "ubicacion", "descripcion", 
            "url", "litros_detectados", "ala_detectada", "cumple_criterios", "fecha_publicacion", "envio", "estado", "imagen"
        ]
        
        # Limpiar log de errores anterior si existe
        if os.path.exists(args.failed_log):
            os.remove(args.failed_log)

        # Sincronización inteligente de anuncios inactivos (vendidos o borrados)
        # Solo lo hacemos si la búsqueda fue representativa/completa (no se llegó al límite configurado y no hubo bloqueo actual)
        is_blocked = scraper.check_for_blocks()
        is_search_complete = (args.limit <= 0) or (len(urls) < args.limit)
        
        if (is_search_complete or args.manual_scroll) and not is_blocked:
            urls_set = set(urls)
            inactive_urls = [u for u in existing_ads if u not in urls_set]
            if inactive_urls:
                logger.info(f"Se detectaron {len(inactive_urls)} anuncios guardados que ya no están activos en Wallapop. Se eliminarán.")
                for u in inactive_urls:
                    existing_ads.pop(u, None)
                # Guardar base de datos limpia inmediatamente
                try:
                    temp_output = args.output + ".tmp"
                    with open(temp_output, 'w', newline='', encoding='utf-8') as csvfile:
                        writer = csv.DictWriter(csvfile, fieldnames=csv_columns, delimiter=';')
                        writer.writeheader()
                        for ad_data in existing_ads.values():
                            writer.writerow(ad_data)
                    os.replace(temp_output, args.output)
                    
                    # Actualizar reporte HTML interactivo
                    try:
                        html_output = os.path.join(os.path.dirname(args.output) or ".", "index.html")
                        generate_html_report(args.output, html_output)
                    except Exception as e:
                        logger.debug(f"No se pudo actualizar el reporte HTML tras purgar inactivos: {e}")
                except Exception as e:
                    logger.error(f"Error al escribir la base de datos limpia tras purga de inactivos: {e}")
        else:
            logger.info("La búsqueda no fue completa (se alcanzó el límite o hubo interrupción/bloqueo). No se purgarán los anuncios inactivos para evitar pérdida de datos.")

        # Filtrar URLs para procesar omitiendo las ya existentes (a menos que se active force-update)
        if args.force_update:
            urls_to_process = urls
            logger.info(f"Modo `--force-update` activo. Se procesarán y actualizarán las {len(urls_to_process)} URLs encontradas.")
        else:
            urls_to_process = [u for u in urls if u not in existing_ads]
            logger.info(f"De las {len(urls)} URLs encontradas, se procesarán {len(urls_to_process)} (omitidas {len(urls) - len(urls_to_process)} ya existentes).")
        
        if not urls_to_process:
            logger.info("No hay anuncios nuevos o a actualizar para procesar. Finalizando.")
            # Generar reporte HTML final por si hubo eliminación de inactivos
            try:
                html_output = os.path.join(os.path.dirname(args.output) or ".", "index.html")
                generate_html_report(args.output, html_output)
            except Exception:
                pass
            return

        # 3. Navegar por cada anuncio
        processed_count = 0
        success_count = 0
        
        for index, url in enumerate(urls_to_process, 1):
            logger.info(f"Procesando anuncio {index}/{len(urls_to_process)}: {url}")
            
            # Espera aleatoria antes de abrir el anuncio para simular comportamiento humano (3-5s)
            wait_time = random.uniform(3.0, 5.0)
            logger.info(f"Esperando {wait_time:.2f}s antes de abrir el anuncio...")
            time.sleep(wait_time)
            
            try:
                scraper.driver.get(url)
                
                # Verificar bloqueos inmediatamente después de la carga
                if scraper.check_for_blocks():
                    logger.error("¡Bloqueo anti-bot (Cloudflare/403) detectado! Deteniendo la ejecución para evitar baneos persistentes.")
                    # Escribir en log de fallos
                    with open(args.failed_log, 'a', encoding='utf-8') as f_log:
                        f_log.write(f"{url} | BLOQUEO ANTI-BOT DETECTADO\n")
                    break
                
                # Aceptar cookies si aparecen en el detalle del anuncio
                scraper.accept_cookies()
                
                # Extraer datos de la página
                data = DataExtractor.parse_listing_page(scraper.driver, url)
                
                # Si no logramos extraer datos básicos (por ejemplo, título vacío), reportarlo
                if not data["titulo"]:
                    raise ValueError("No se pudo extraer el título del anuncio.")
                
                # Procesar Regex sobre Título + Descripción
                text_to_search = f"{data['titulo']} {data['descripcion']}"
                liters = DataExtractor.extract_liters(text_to_search)
                wings = DataExtractor.extract_wing_size(text_to_search)
                
                # Evaluar criterios: litros > min_liters Y ala en el rango [min_wing, max_wing]
                has_liters_match = any(l > args.min_liters for l in liters)
                has_wing_match = any(args.min_wing <= w <= args.max_wing for w in wings)
                cumple = has_liters_match and has_wing_match
                
                # Agregar campos procesados al diccionario de datos
                data["litros_detectados"] = str(liters)
                data["ala_detectada"] = str(wings)
                data["cumple_criterios"] = cumple
                
                # Actualizar base de datos en memoria y reescribir atómicamente
                existing_ads[url] = data
                
                try:
                    temp_output = args.output + ".tmp"
                    with open(temp_output, 'w', newline='', encoding='utf-8') as csvfile:
                        writer = csv.DictWriter(csvfile, fieldnames=csv_columns, delimiter=';')
                        writer.writeheader()
                        for ad_data in existing_ads.values():
                            writer.writerow(ad_data)
                    os.replace(temp_output, args.output)
                except Exception as e:
                    logger.error(f"Error al escribir de forma atómica en el CSV: {e}")
                
                success_count += 1
                logger.info(f"Anuncio procesado con éxito. Cumple criterios: {cumple}")
                
                # Actualizar reporte HTML interactivo
                try:
                    html_output = os.path.join(os.path.dirname(args.output) or ".", "index.html")
                    generate_html_report(args.output, html_output)
                except Exception as e:
                    logger.debug(f"No se pudo actualizar el reporte HTML: {e}")
                
            except Exception as e:
                logger.error(f"Error procesando el anuncio {url}: {e}")
                # Registrar error en failed_listings.log
                with open(args.failed_log, 'a', encoding='utf-8') as f_log:
                    f_log.write(f"{url} | Error: {str(e)}\n")
                    
            processed_count += 1
            
        logger.info("=== PROCESO COMPLETADO ===")
        logger.info(f"Anuncios totales encontrados: {len(urls)}")
        logger.info(f"Anuncios nuevos procesados con éxito: {success_count}")
        logger.info(f"Anuncios nuevos con fallos: {processed_count - success_count}")
        if processed_count < len(urls_to_process):
            logger.warning(f"La ejecución se detuvo antes de finalizar debido a un bloqueo o interrupción. Restaban {len(urls_to_process) - processed_count} anuncios nuevos.")
            
        # Generación final de reporte HTML por seguridad
        try:
            html_output = os.path.join(os.path.dirname(args.output) or ".", "index.html")
            generate_html_report(args.output, html_output)
            logger.info(f"Reporte HTML interactivo generado en: {html_output}")
        except Exception:
            pass
            
    finally:
        scraper.close()


def generate_html_report(csv_path, html_path):
    """
    Lee el archivo CSV delimitado por punto y coma (;) y genera/actualiza 
    una página web interactiva (dashboard) en formato HTML para ver, 
    ordenar y filtrar los resultados de forma visual.
    """
    import csv
    import json
    import os
    
    if not os.path.exists(csv_path):
        return
        
    listings = []
    try:
        with open(csv_path, 'r', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile, delimiter=';')
            for row in reader:
                # Convertir booleano de cumplimiento
                row['cumple_criterios'] = row.get('cumple_criterios') == 'True'
                listings.append(row)
    except Exception as e:
        logger.error(f"Error leyendo CSV para el reporte HTML: {e}")
        return
        
    listings_json = json.dumps(listings, ensure_ascii=False)
    
    html_content = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Wallapop Wingfoil Dashboard</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-color: #0b0f19;
            --card-bg: #151b2d;
            --text-color: #f3f4f6;
            --text-muted: #9ca3af;
            --primary: #10b981;
            --primary-glow: rgba(16, 185, 129, 0.15);
            --accent: #d97706;
            --danger: #ef4444;
            --gold: #fbbf24;
            --gold-glow: rgba(251, 191, 36, 0.25);
            --border-color: #1f2937;
        }}
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}
        body {{
            background-color: var(--bg-color);
            color: var(--text-color);
            font-family: 'Outfit', sans-serif;
            padding: 2rem;
            line-height: 1.5;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 2rem;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 1.5rem;
            flex-wrap: wrap;
            gap: 1rem;
        }}
        h1 {{
            font-size: 2.2rem;
            font-weight: 700;
            background: linear-gradient(135deg, #60a5fa, #10b981);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .stats {{
            display: flex;
            gap: 1.5rem;
            margin-bottom: 2rem;
            flex-wrap: wrap;
            width: 100%;
        }}
        .stat-card {{
            background-color: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 1.2rem;
            flex: 1;
            min-width: 200px;
            text-align: center;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
        }}
        .stat-card h3 {{
            font-size: 0.85rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 0.5rem;
        }}
        .stat-card p {{
            font-size: 2rem;
            font-weight: 700;
            color: #ffffff;
        }}
        .stat-card.highlight p {{
            color: var(--gold);
            text-shadow: 0 0 10px rgba(251, 191, 36, 0.2);
        }}
        .controls {{
            background-color: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 1.5rem;
            margin-bottom: 2rem;
            display: flex;
            gap: 1rem;
            flex-wrap: wrap;
            align-items: center;
        }}
        .search-box {{
            flex: 1;
            min-width: 280px;
        }}
        .search-box input {{
            width: 100%;
            padding: 0.8rem 1.2rem;
            background-color: var(--bg-color);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            color: var(--text-color);
            font-size: 1rem;
            font-family: inherit;
            outline: none;
            transition: border-color 0.2s;
        }}
        .search-box input:focus {{
            border-color: var(--primary);
        }}
        .filter-group {{
            display: flex;
            gap: 0.5rem;
            flex-wrap: wrap;
        }}
        .filter-btn {{
            background-color: var(--bg-color);
            border: 1px solid var(--border-color);
            color: var(--text-color);
            padding: 0.6rem 1.2rem;
            border-radius: 8px;
            cursor: pointer;
            font-size: 0.9rem;
            font-family: inherit;
            transition: all 0.2s;
        }}
        .filter-btn:hover {{
            border-color: var(--primary);
        }}
        .filter-btn.active {{
            background-color: var(--primary);
            border-color: var(--primary);
            color: #0b0f19;
            font-weight: 600;
        }}
        .sort-select {{
            padding: 0.75rem 1.2rem;
            background-color: var(--bg-color);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            color: var(--text-color);
            font-size: 0.9rem;
            font-family: inherit;
            outline: none;
            cursor: pointer;
        }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(350px, 1fr));
            gap: 1.5rem;
        }}
        .card {{
            background-color: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
            transition: transform 0.2s, border-color 0.2s;
            position: relative;
            overflow: hidden;
        }}
        .card:hover {{
            transform: translateY(-4px);
            border-color: #3b82f6;
        }}
        .card.match {{
            border: 2px solid var(--gold);
            box-shadow: 0 0 15px var(--gold-glow);
        }}
        .card-body {{
            padding: 1.2rem;
            display: flex;
            flex-direction: column;
            flex-grow: 1;
            justify-content: space-between;
        }}
        .card-image {{
            width: 100%;
            height: 200px;
            object-fit: cover;
            border-bottom: 1px solid var(--border-color);
            display: block;
            background-color: #0b0f19;
        }}
        .card-image-placeholder {{
            width: 100%;
            height: 200px;
            background: linear-gradient(135deg, #111827, #1f2937);
            display: flex;
            align-items: center;
            justify-content: center;
            color: var(--text-muted);
            font-size: 0.85rem;
            border-bottom: 1px solid var(--border-color);
        }}
        .card-header {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 0.75rem;
            gap: 0.5rem;
        }}
        .card-title {{
            font-size: 1.25rem;
            font-weight: 600;
            color: #ffffff;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
            line-height: 1.3;
        }}
        .card-price {{
            font-size: 1.3rem;
            font-weight: 700;
            color: var(--primary);
            background-color: var(--primary-glow);
            padding: 0.2rem 0.6rem;
            border-radius: 6px;
            white-space: nowrap;
        }}
        .card-meta {{
            font-size: 0.85rem;
            color: var(--text-muted);
            margin-bottom: 1rem;
            display: flex;
            justify-content: space-between;
        }}
        .card-badges {{
            display: flex;
            gap: 0.5rem;
            flex-wrap: wrap;
            margin-bottom: 1rem;
        }}
        .badge {{
            font-size: 0.72rem;
            font-weight: 600;
            padding: 0.25rem 0.5rem;
            border-radius: 4px;
            text-transform: uppercase;
        }}
        .badge-status {{
            background-color: rgba(16, 185, 129, 0.1);
            color: var(--primary);
            border: 1px solid rgba(16, 185, 129, 0.2);
        }}
        .badge-status.reservado {{
            background-color: rgba(217, 119, 6, 0.1);
            color: var(--accent);
            border: 1px solid rgba(217, 119, 6, 0.2);
        }}
        .badge-status.vendido {{
            background-color: rgba(239, 68, 68, 0.1);
            color: var(--danger);
            border: 1px solid rgba(239, 68, 68, 0.2);
        }}
        .badge-shipping {{
            background-color: rgba(139, 92, 246, 0.1);
            color: #a78bfa;
            border: 1px solid rgba(139, 92, 246, 0.2);
        }}
        .badge-shipping.no {{
            background-color: rgba(156, 163, 175, 0.1);
            color: var(--text-muted);
            border: 1px solid rgba(156, 163, 175, 0.2);
        }}
        .badge-info {{
            background-color: rgba(59, 130, 246, 0.1);
            color: #60a5fa;
            border: 1px solid rgba(59, 130, 246, 0.2);
        }}
        .badge-criteria {{
            background-color: var(--gold-glow);
            color: var(--gold);
            border: 1px solid var(--gold);
            animation: pulse 2s infinite;
        }}
        @keyframes pulse {{
            0% {{ box-shadow: 0 0 0 0 rgba(251, 191, 36, 0.4); }}
            70% {{ box-shadow: 0 0 0 6px rgba(251, 191, 36, 0); }}
            100% {{ box-shadow: 0 0 0 0 rgba(251, 191, 36, 0); }}
        }}
        .card-description {{
            font-size: 0.9rem;
            color: var(--text-muted);
            margin-bottom: 1.5rem;
            display: -webkit-box;
            -webkit-line-clamp: 3;
            -webkit-box-orient: vertical;
            overflow: hidden;
            cursor: pointer;
            transition: color 0.2s;
            white-space: pre-line;
        }}
        .card-description:hover {{
            color: var(--text-color);
        }}
        .card-description.expanded {{
            display: block;
            overflow: visible;
            -webkit-line-clamp: unset;
        }}
        .card-footer {{
            margin-top: auto;
            border-top: 1px solid var(--border-color);
            padding-top: 1rem;
            display: flex;
            justify-content: flex-end;
        }}
        .btn-link {{
            display: inline-block;
            background: linear-gradient(135deg, #3b82f6, #10b981);
            color: #0b0f19;
            text-decoration: none;
            padding: 0.6rem 1.2rem;
            border-radius: 6px;
            font-size: 0.9rem;
            font-weight: 600;
            transition: opacity 0.2s, transform 0.1s;
            text-align: center;
        }}
        .btn-link:hover {{
            opacity: 0.9;
            transform: scale(1.02);
        }}
        .no-results {{
            text-align: center;
            padding: 4rem 2rem;
            color: var(--text-muted);
            font-size: 1.2rem;
            grid-column: 1 / -1;
            background-color: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 12px;
        }}
        .card-image-container {{
            position: relative;
            cursor: pointer;
            overflow: hidden;
            border-top-left-radius: 12px;
            border-top-right-radius: 12px;
        }}
        .card-image-overlay {{
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background-color: rgba(11, 15, 25, 0.4);
            color: #ffffff;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 0.9rem;
            font-weight: 600;
            opacity: 0;
            transition: opacity 0.2s;
        }}
        .card-image-container:hover .card-image-overlay {{
            opacity: 1;
        }}
        .badge-photos-count {{
            position: absolute;
            bottom: 10px;
            right: 10px;
            background-color: rgba(11, 15, 25, 0.85);
            color: #ffffff;
            padding: 0.2rem 0.5rem;
            border-radius: 4px;
            font-size: 0.75rem;
            font-weight: 600;
            border: 1px solid var(--border-color);
        }}
        /* Modal Lightbox */
        .modal {{
            display: none;
            position: fixed;
            z-index: 1000;
            left: 0;
            top: 0;
            width: 100%;
            height: 100%;
            background-color: rgba(11, 15, 25, 0.95);
            align-items: center;
            justify-content: center;
        }}
        .modal-content-wrapper {{
            position: relative;
            max-width: 90%;
            max-height: 85%;
            display: flex;
            flex-direction: column;
            align-items: center;
        }}
        .modal-img {{
            max-width: 100%;
            max-height: 70vh;
            object-fit: contain;
            border-radius: 8px;
            box-shadow: 0 0 30px rgba(0, 0, 0, 0.6);
        }}
        .close-btn {{
            position: absolute;
            top: -45px;
            right: 0;
            color: #ffffff;
            font-size: 2.5rem;
            font-weight: bold;
            cursor: pointer;
            transition: color 0.2s;
            user-select: none;
        }}
        .close-btn:hover {{
            color: var(--primary);
        }}
        .nav-btn {{
            position: absolute;
            top: 50%;
            transform: translateY(-50%);
            background-color: rgba(21, 27, 45, 0.7);
            border: 1px solid var(--border-color);
            color: #ffffff;
            font-size: 2rem;
            width: 50px;
            height: 50px;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            border-radius: 50%;
            transition: all 0.2s;
            user-select: none;
            outline: none;
        }}
        .nav-btn:hover {{
            background-color: var(--primary);
            color: #0b0f19;
            border-color: var(--primary);
        }}
        .prev-btn {{
            left: -70px;
        }}
        .next-btn {{
            right: -70px;
        }}
        @media (max-width: 768px) {{
            .prev-btn {{ left: 10px; }}
            .next-btn {{ right: 10px; }}
            .modal-img {{ max-height: 60vh; }}
            
            /* Mejoras de texto responsivo para móvil */
            body {{
                font-size: 16px;
            }}
            .card-title {{
                font-size: 1.35rem;
            }}
            .card-description {{
                font-size: 1.05rem;
            }}
            .card-meta {{
                font-size: 0.95rem;
            }}
            .badge {{
                font-size: 0.8rem;
                padding: 0.35rem 0.6rem;
            }}
            .btn-link {{
                font-size: 1.05rem;
                padding: 0.8rem 1.4rem;
                width: 100%;
                text-align: center;
            }}
            
            /* Textos más grandes en el panel de filtros en móvil */
            .controls-panel label, 
            .controls-panel input, 
            .controls-panel select, 
            .controls-panel button,
            .controls-panel span {{
                font-size: 1.05rem !important;
            }}
            
            .controls-header span {{
                font-size: 1.25rem !important;
            }}
            
            .filter-btn {{
                padding: 0.8rem 1.2rem !important;
            }}
        }}
        .modal-info {{
            margin-top: 1rem;
            color: var(--text-color);
            font-size: 1rem;
            text-align: center;
            font-weight: 600;
        }}
        .thumbnails-container {{
            display: flex;
            gap: 0.5rem;
            margin-top: 1rem;
            overflow-x: auto;
            max-width: 100%;
            padding: 0.5rem;
        }}
        .thumb-img {{
            width: 50px;
            height: 50px;
            object-fit: cover;
            border-radius: 4px;
            cursor: pointer;
            opacity: 0.5;
            border: 2px solid transparent;
            transition: all 0.2s;
        }}
        .thumb-img.active, .thumb-img:hover {{
            opacity: 1;
            border-color: var(--primary);
        }}
        .card-actions {{
            position: absolute;
            top: 10px;
            left: 10px;
            display: flex;
            gap: 0.4rem;
            z-index: 10;
        }}
        .action-btn {{
            background-color: rgba(11, 15, 25, 0.85);
            border: 1px solid var(--border-color);
            color: #ffffff;
            width: 38px;
            height: 38px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            font-size: 1.25rem;
            transition: all 0.2s;
            outline: none;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.4);
        }}
        .action-btn:hover {{
            border-color: var(--primary);
            transform: scale(1.1);
        }}
        .action-btn.fav.active {{
            color: var(--gold);
            border-color: var(--gold);
            background-color: rgba(251, 191, 36, 0.15);
        }}
        .action-btn.dismiss:hover {{
            color: var(--danger);
            border-color: var(--danger);
            background-color: rgba(239, 68, 68, 0.15);
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div>
                <h1>Wallapop Wingfoil Dashboard</h1>
                <p style="color: var(--text-muted); font-size: 0.9rem; margin-top: 0.2rem;">Panel interactivo de resultados obtenidos en tiempo real</p>
            </div>
            <div id="update-timestamp" style="font-size: 0.85rem; color: var(--text-muted); text-align: right;"></div>
        </header>

        <div class="stats" style="margin-bottom: 2rem;">
            <div class="stat-card" style="min-width: 260px; flex: 1.2;">
                <h3>Resumen de Anuncios</h3>
                <div style="display: flex; justify-content: space-around; align-items: center; margin-top: 0.5rem; gap: 0.5rem;">
                    <div style="text-align: center; flex: 1;">
                        <span style="font-size: 0.75rem; color: var(--text-muted); text-transform: uppercase;">Total</span>
                        <p id="stat-total" style="font-size: 1.5rem; color: #ffffff; font-weight: 700; margin-top: 0.1rem;">0</p>
                    </div>
                    <div style="text-align: center; border-left: 1px solid var(--border-color); border-right: 1px solid var(--border-color); flex: 1.2; padding: 0 0.5rem;">
                        <span style="font-size: 0.75rem; color: var(--text-muted); text-transform: uppercase;">Disponibles</span>
                        <p id="stat-available" style="font-size: 1.5rem; color: #34d399; font-weight: 700; margin-top: 0.1rem;">0</p>
                    </div>
                    <div style="text-align: center; flex: 1;">
                        <span style="font-size: 0.75rem; color: var(--text-muted); text-transform: uppercase;">Con Envío</span>
                        <p id="stat-shippable" style="font-size: 1.5rem; color: #a78bfa; font-weight: 700; margin-top: 0.1rem;">0</p>
                    </div>
                </div>
            </div>
            <div class="stat-card" style="min-width: 260px; flex: 1.5; border-color: rgba(16, 185, 129, 0.15);">
                <h3>Precios (Mín / Prom / Máx)</h3>
                <div style="display: flex; justify-content: space-around; align-items: center; margin-top: 0.5rem; gap: 0.5rem;">
                    <div style="text-align: center; flex: 1;">
                        <span style="font-size: 0.75rem; color: var(--text-muted); text-transform: uppercase;">Mín</span>
                        <p id="price-min" style="font-size: 1.5rem; color: var(--primary); font-weight: 700; margin-top: 0.1rem;">0€</p>
                    </div>
                    <div style="text-align: center; border-left: 1px solid var(--border-color); border-right: 1px solid var(--border-color); flex: 1.2; padding: 0 0.5rem;">
                        <span style="font-size: 0.75rem; color: var(--text-muted); text-transform: uppercase;">Prom</span>
                        <p id="price-avg" style="font-size: 1.5rem; color: #60a5fa; font-weight: 700; margin-top: 0.1rem;">0€</p>
                    </div>
                    <div style="text-align: center; flex: 1;">
                        <span style="font-size: 0.75rem; color: var(--text-muted); text-transform: uppercase;">Máx</span>
                        <p id="price-max" style="font-size: 1.5rem; color: var(--danger); font-weight: 700; margin-top: 0.1rem;">0€</p>
                    </div>
                </div>
            </div>
        </div>

        <div class="controls-panel" style="background-color: var(--card-bg); border: 1px solid var(--border-color); border-radius: 12px; padding: 1.2rem; margin-bottom: 2rem;">
            <div class="controls-header" style="display: flex; justify-content: space-between; align-items: center; cursor: pointer; user-select: none;" onclick="toggleFilters()">
                <span style="font-weight: 700; font-size: 1.15rem; display: flex; align-items: center; gap: 0.5rem; color: #ffffff;">
                    🔍 Panel de Filtros y Búsqueda
                </span>
                <button id="toggle-filters-btn" style="background: transparent; border: 1px solid var(--border-color); border-radius: 6px; color: var(--text-muted); padding: 0.4rem 0.8rem; font-size: 0.9rem; cursor: pointer; font-weight: 600; transition: all 0.2s;">
                    Ocultar Filtros
                </button>
            </div>
            
            <div id="filters-container" style="margin-top: 1.2rem; transition: max-height 0.3s ease-out, opacity 0.3s ease-out; overflow: hidden; max-height: 1200px; opacity: 1;">
            <!-- Fila 1: Búsqueda y Limpieza -->
            <div style="display: flex; gap: 1rem; width: 100%; flex-wrap: wrap; margin-bottom: 1rem;">
                <div class="search-box">
                    <input type="text" id="search-input" placeholder="Buscar por título, ubicación o descripción...">
                </div>
                <button class="filter-btn" onclick="resetFilters()" style="background-color: rgba(239, 68, 68, 0.1); border-color: rgba(239, 68, 68, 0.2); color: var(--danger); font-weight: 600; padding: 0.8rem 1.5rem;">Limpiar Filtros</button>
            </div>
            
            <!-- Fila 2: Rangos numéricos y Ordenación -->
            <div style="display: flex; gap: 1rem; width: 100%; flex-wrap: wrap; justify-content: space-between; align-items: center; margin-bottom: 1rem;">
                <div style="display: flex; gap: 0.8rem; flex-wrap: wrap; align-items: center; border: 1px solid var(--border-color); padding: 0.6rem 1rem; border-radius: 8px; background-color: rgba(0,0,0,0.15);">
                    <div style="display: flex; align-items: center; gap: 0.4rem;">
                        <label for="liters-min" style="font-size: 0.85rem; color: var(--text-muted); font-weight: 600;">Litros Mín:</label>
                        <input type="number" id="liters-min" placeholder="Ej. 80" style="width: 70px; padding: 0.4rem 0.5rem; background-color: var(--bg-color); border: 1px solid var(--border-color); border-radius: 6px; color: var(--text-color); font-family: inherit; font-size: 0.9rem; outline: none;">
                    </div>
                    <div style="display: flex; align-items: center; gap: 0.4rem;">
                        <label for="liters-max" style="font-size: 0.85rem; color: var(--text-muted); font-weight: 600;">Litros Máx:</label>
                        <input type="number" id="liters-max" placeholder="Ej. 120" style="width: 70px; padding: 0.4rem 0.5rem; background-color: var(--bg-color); border: 1px solid var(--border-color); border-radius: 6px; color: var(--text-color); font-family: inherit; font-size: 0.9rem; outline: none;">
                    </div>
                    <div style="display: flex; align-items: center; gap: 0.4rem; border-left: 1px solid var(--border-color); padding-left: 0.8rem;">
                        <label for="wing-min" style="font-size: 0.85rem; color: var(--text-muted); font-weight: 600;">Ala Mín:</label>
                        <input type="number" step="0.1" id="wing-min" placeholder="Ej. 4" style="width: 65px; padding: 0.4rem 0.5rem; background-color: var(--bg-color); border: 1px solid var(--border-color); border-radius: 6px; color: var(--text-color); font-family: inherit; font-size: 0.9rem; outline: none;">
                    </div>
                    <div style="display: flex; align-items: center; gap: 0.4rem;">
                        <label for="wing-max" style="font-size: 0.85rem; color: var(--text-muted); font-weight: 600;">Ala Máx:</label>
                        <input type="number" step="0.1" id="wing-max" placeholder="Ej. 6" style="width: 65px; padding: 0.4rem 0.5rem; background-color: var(--bg-color); border: 1px solid var(--border-color); border-radius: 6px; color: var(--text-color); font-family: inherit; font-size: 0.9rem; outline: none;">
                    </div>
                    <div style="display: flex; align-items: center; gap: 0.4rem; border-left: 1px solid var(--border-color); padding-left: 0.8rem;">
                        <label for="price-min-filter" style="font-size: 0.85rem; color: var(--text-muted); font-weight: 600;">Precio Mín:</label>
                        <input type="number" id="price-min-filter" placeholder="Ej. 100" style="width: 70px; padding: 0.4rem 0.5rem; background-color: var(--bg-color); border: 1px solid var(--border-color); border-radius: 6px; color: var(--text-color); font-family: inherit; font-size: 0.9rem; outline: none;">
                    </div>
                    <div style="display: flex; align-items: center; gap: 0.4rem;">
                        <label for="price-max-filter" style="font-size: 0.85rem; color: var(--text-muted); font-weight: 600;">Precio Máx:</label>
                        <input type="number" id="price-max-filter" placeholder="Ej. 500" style="width: 70px; padding: 0.4rem 0.5rem; background-color: var(--bg-color); border: 1px solid var(--border-color); border-radius: 6px; color: var(--text-color); font-family: inherit; font-size: 0.9rem; outline: none;">
                    </div>
                </div>
                
                <select class="sort-select" id="sort-select">
                    <option value="default">Relevancia por defecto</option>
                    <option value="price-asc">Precio: de Menor a Mayor</option>
                    <option value="price-desc">Precio: de Mayor a Menor</option>
                    <option value="liters-asc">Litros: de Menor a Mayor</option>
                    <option value="liters-desc">Litros: de Mayor a Menor</option>
                    <option value="wing-asc">Tamaño de Ala: de Menor a Mayor</option>
                    <option value="wing-desc">Tamaño de Ala: de Mayor a Menor</option>
                    <option value="date-desc">Fecha: Más recientes</option>
                </select>
            </div>

            <!-- Fila 3: Botones de Categorías y Tipos de Material -->
            <div style="display: flex; gap: 1rem; width: 100%; flex-wrap: wrap; justify-content: space-between; align-items: center; border-top: 1px solid var(--border-color); padding-top: 1rem;">
                <!-- Filtros Principales -->
                <div class="filter-group">
                    <button class="filter-btn active" data-filter="all">Todos</button>
                    <button class="filter-btn" data-filter="matches">Cumplen Criterios</button>
                    <button class="filter-btn" data-filter="available">Solo Disponibles</button>
                    <button class="filter-btn" data-filter="shipping">Admite Envío</button>
                    <button class="filter-btn" data-filter="favs" style="color: var(--gold); border-color: rgba(251,191,36,0.35);">⭐ Favoritos</button>
                    <button class="filter-btn" data-filter="dismissed" style="color: var(--text-muted); border-color: var(--border-color);">🗑️ Descartes</button>
                </div>

                <!-- Filtros de Categoría -->
                <div class="filter-group" id="category-filter-group">
                    <button class="filter-btn active" data-category="all">Todo</button>
                    <button class="filter-btn" data-category="board">Tablas</button>
                    <button class="filter-btn" data-category="wing">Wings / Alas</button>
                    <button class="filter-btn" data-category="foil">Foils</button>
                    <button class="filter-btn" data-category="other">Accesorios/Otros</button>
                </div>
            </div>
        </div>
    </div>

        <div id="filter-status" style="font-size: 0.95rem; color: var(--text-muted); margin-top: 1.5rem; margin-bottom: 1rem; font-weight: 600; display: flex; justify-content: space-between; align-items: center;">
            <span id="results-count">Mostrando 0 de 0 anuncios</span>
        </div>

        <div class="grid" id="listings-grid">
            <!-- Cards rendered here by Javascript -->
        </div>
    </div>

    <script>
        const listings = {listings_json};

        // Render Stats
        document.getElementById('stat-total').innerText = listings.length;
        document.getElementById('stat-available').innerText = listings.filter(x => x.estado === 'Disponible').length;
        document.getElementById('stat-shippable').innerText = listings.filter(x => x.envio === 'Envío disponible').length;
        document.getElementById('update-timestamp').innerText = "Actualizado: " + new Date().toLocaleTimeString();

        // Cargar listas de Favoritos y Descartes desde LocalStorage
        let favorites = JSON.parse(localStorage.getItem('wallapop_favorites') || '[]');
        let dismissed = JSON.parse(localStorage.getItem('wallapop_dismissed') || '[]');

        let currentFilter = 'all';
        let currentCategory = 'all';
        let searchQuery = '';
        let currentSort = 'default';

        window.toggleFav = function(url, event) {{
            if (event) event.stopPropagation();
            const idx = favorites.indexOf(url);
            if (idx > -1) {{
                favorites.splice(idx, 1);
            }} else {{
                favorites.push(url);
            }}
            localStorage.setItem('wallapop_favorites', JSON.stringify(favorites));
            renderListings();
        }};

        window.dismissItem = function(url, event) {{
            if (event) event.stopPropagation();
            if (!dismissed.includes(url)) {{
                dismissed.push(url);
                localStorage.setItem('wallapop_dismissed', JSON.stringify(dismissed));
            }}
            const favIdx = favorites.indexOf(url);
            if (favIdx > -1) {{
                favorites.splice(favIdx, 1);
                localStorage.setItem('wallapop_favorites', JSON.stringify(favorites));
            }}
            renderListings();
        }};

        window.restoreItem = function(url, event) {{
            if (event) event.stopPropagation();
            const idx = dismissed.indexOf(url);
            if (idx > -1) {{
                dismissed.splice(idx, 1);
                localStorage.setItem('wallapop_dismissed', JSON.stringify(dismissed));
            }}
            renderListings();
        }};

        window.resetFilters = function() {{
            document.getElementById('search-input').value = '';
            document.getElementById('liters-min').value = '';
            document.getElementById('liters-max').value = '';
            document.getElementById('wing-min').value = '';
            document.getElementById('wing-max').value = '';
            document.getElementById('price-min-filter').value = '';
            document.getElementById('price-max-filter').value = '';
            searchQuery = '';
            renderListings();
        }};

        window.toggleFilters = function() {{
            const container = document.getElementById('filters-container');
            const btn = document.getElementById('toggle-filters-btn');
            if (container.style.maxHeight === '0px') {{
                container.style.maxHeight = '1200px';
                container.style.opacity = '1';
                btn.innerText = 'Ocultar Filtros';
            }} else {{
                container.style.maxHeight = '0px';
                container.style.opacity = '0';
                btn.innerText = 'Mostrar Filtros';
            }}
        }};
        
        // Helper para extraer los números de los campos stringificados como "[74.5]" o "[]"
        function parseDetectedValues(str) {{
            if (!str || str === '[]') return [];
            try {{
                // Si viene como JSON array literal
                const parsed = JSON.parse(str.replace(/'/g, '"'));
                return Array.isArray(parsed) ? parsed.map(Number) : [];
            }} catch(e) {{
                // Fallback manual si no es JSON válido
                const matches = str.match(/\\d+(?:\\.\\d+)?/g);
                return matches ? matches.map(Number) : [];
            }}
        }}

        // Clasificador inteligente de productos en base a su título y descripción (sistema de puntuación)
        function getListingType(item) {{
            const title = (item.titulo || "").toLowerCase();
            const desc = (item.descripcion || "").toLowerCase();
            const text = title + " " + desc;
            
            let boardScore = 0;
            let wingScore = 0;
            let foilScore = 0;
            
            // 1. Detecciones físicas del scraper (peso muy alto)
            const hasLiters = parseDetectedValues(item.litros_detectados).length > 0;
            const hasWing = parseDetectedValues(item.ala_detectada).length > 0;
            if (hasLiters) boardScore += 10;
            if (hasWing) wingScore += 10;
            
            // 2. Coincidencias en el título (peso alto)
            // Palabras de tabla
            if (title.includes("tabla") || title.includes("board") || title.includes("foilboard")) boardScore += 8;
            if (title.includes("litros") || title.includes(" lts")) boardScore += 5;
            
            // Palabras de wing
            if (title.includes("wing") && !title.includes("wingfoil")) wingScore += 8;
            if (title.includes("ala") && !title.includes("ala de foil")) wingScore += 8;
            if (title.includes("vela")) wingScore += 6;
            
            // Palabras de foil
            if (title.includes("foil") && !title.includes("wingfoil") && !title.includes("tabla")) foilScore += 8;
            if (title.includes("hydrofoil")) foilScore += 8;
            if (title.includes("mastil") || title.includes("mástil") || title.includes("mast")) foilScore += 7;
            if (title.includes("estabilizador") || title.includes("stabilizer") || title.includes("stab")) foilScore += 7;
            if (title.includes("fuselaje") || title.includes("fuselage")) foilScore += 7;
            if (title.includes("frontwing") || title.includes("front wing")) foilScore += 7;
            if (title.includes("sabfoil") || title.includes("moses") || title.includes("levitator")) foilScore += 9;
            
            // 3. Coincidencias en la descripción (peso medio/bajo)
            if (desc.includes("tabla de wing") || desc.includes("tabla foil")) boardScore += 4;
            if (desc.includes("ala wing") || desc.includes("ala de wing")) wingScore += 4;
            if (desc.includes("foil completo") || desc.includes("mástil") || desc.includes("estabilizador")) foilScore += 4;
            
            // Caso especial: si solo dice wingfoil a secas (pack o equipo completo)
            if (title.includes("wingfoil") && boardScore === 0 && wingScore === 0 && foilScore === 0) {{
                return "board"; // suele centrarse en la tabla/pack
            }}
            
            const maxScore = Math.max(boardScore, wingScore, foilScore);
            if (maxScore > 0) {{
                if (maxScore === boardScore) return "board";
                if (maxScore === wingScore) return "wing";
                if (maxScore === foilScore) return "foil";
            }}
            
            return "other";
        }}

        function renderListings() {{
            const grid = document.getElementById('listings-grid');
            grid.innerHTML = '';

            // Obtener valores de los nuevos filtros numéricos
            const minLitersVal = parseFloat(document.getElementById('liters-min').value) || 0;
            const maxLitersVal = parseFloat(document.getElementById('liters-max').value) || Infinity;
            const minWingVal = parseFloat(document.getElementById('wing-min').value) || 0;
            const maxWingVal = parseFloat(document.getElementById('wing-max').value) || Infinity;
            const minPriceVal = parseFloat(document.getElementById('price-min-filter').value) || 0;
            const maxPriceVal = parseFloat(document.getElementById('price-max-filter').value) || Infinity;

            const hasActiveCriteria = (minLitersVal > 0 || maxLitersVal < Infinity || minWingVal > 0 || maxWingVal < Infinity);

            function matchesActiveCriteria(item) {{
                if (minLitersVal > 0 || maxLitersVal < Infinity) {{
                    const lits = parseDetectedValues(item.litros_detectados);
                    if (lits.length === 0 || !lits.some(l => l >= minLitersVal && l <= maxLitersVal)) {{
                        return false;
                    }}
                }}
                if (minWingVal > 0 || maxWingVal < Infinity) {{
                    const wings = parseDetectedValues(item.ala_detectada);
                    if (wings.length === 0 || !wings.some(w => w >= minWingVal && w <= maxWingVal)) {{
                        return false;
                    }}
                }}
                return true;
            }}

            function isHighlighted(item) {{
                if (hasActiveCriteria) {{
                    return matchesActiveCriteria(item);
                }}
                return item.cumple_criterios;
            }}

            // Filtrar anuncios primero
            let filtered = listings.filter(item => {{
                // Lógica de visualización de descartados
                const isDismissed = dismissed.includes(item.url);
                if (currentFilter === 'dismissed') {{
                    if (!isDismissed) return false;
                }} else {{
                    if (isDismissed) return false; // Ocultar descartados por defecto
                }}

                // Filtro de Favoritos
                if (currentFilter === 'favs') {{
                    if (!favorites.includes(item.url)) return false;
                }}

                // Filtro de botones superiores
                if (currentFilter === 'matches' && !isHighlighted(item)) return false;
                if (currentFilter === 'available' && item.estado !== 'Disponible') return false;
                if (currentFilter === 'shipping' && item.envio !== 'Envío disponible') return false;

                // Filtro por Rango de Litros
                if (minLitersVal > 0 || maxLitersVal < Infinity) {{
                    const lits = parseDetectedValues(item.litros_detectados);
                    if (lits.length === 0 || !lits.some(l => l >= minLitersVal && l <= maxLitersVal)) {{
                        return false;
                    }}
                }}

                // Filtro por Rango de Vela
                if (minWingVal > 0 || maxWingVal < Infinity) {{
                    const wings = parseDetectedValues(item.ala_detectada);
                    if (wings.length === 0 || !wings.some(w => w >= minWingVal && w <= maxWingVal)) {{
                        return false;
                    }}
                }}

                // Filtro por Rango de Precios
                const priceVal = getCleanPrice(item);
                if (priceVal < minPriceVal || priceVal > maxPriceVal) return false;

                // Filtro de Categoría
                if (currentCategory !== 'all') {{
                    const itemType = getListingType(item);
                    if (itemType !== currentCategory) return false;
                }}

                // Filtro de búsqueda de texto
                if (searchQuery) {{
                    const query = searchQuery.toLowerCase();
                    const title = (item.titulo || "").toLowerCase();
                    const desc = (item.descripcion || "").toLowerCase();
                    const loc = (item.ubicacion || "").toLowerCase();
                    return title.includes(query) || desc.includes(query) || loc.includes(query);
                }}
                return true;
            }});

            // Actualizar contador de resultados visibles y tarjetas de estadísticas
            document.getElementById('results-count').innerText = `Mostrando ${{filtered.length}} de ${{listings.length}} anuncios`;
            
            document.getElementById('stat-total').innerText = filtered.length;
            document.getElementById('stat-available').innerText = filtered.filter(x => x.estado === 'Disponible').length;
            document.getElementById('stat-shippable').innerText = filtered.filter(x => x.envio === 'Envío disponible').length;


            // Calcular y actualizar estadísticas de precios de la selección activa
            const activePrices = filtered.map(getCleanPrice).filter(p => p > 0);
            let minPrice = 0, maxPrice = 0, avgPrice = 0;
            if (activePrices.length > 0) {{
                minPrice = Math.min(...activePrices);
                maxPrice = Math.max(...activePrices);
                avgPrice = Math.round(activePrices.reduce((a, b) => a + b, 0) / activePrices.length);
            }}
            document.getElementById('price-min').innerText = minPrice > 0 ? `${{minPrice}}€` : 'N/D';
            document.getElementById('price-max').innerText = maxPrice > 0 ? `${{maxPrice}}€` : 'N/D';
            document.getElementById('price-avg').innerText = avgPrice > 0 ? `${{avgPrice}}€` : 'N/D';

            // Helper para obtener precio limpio para ordenar (evitar NaN que rompe el .sort)
            function getCleanPrice(item) {{
                const val = parseFloat(item.precio);
                return isNaN(val) ? 0 : val;
            }}

            // Helpers para obtener litros y ala máximos para ordenar
            function getMaxLiters(item) {{
                const lits = parseDetectedValues(item.litros_detectados);
                return lits.length > 0 ? Math.max(...lits) : 0;
            }}

            function getMaxWing(item) {{
                const wings = parseDetectedValues(item.ala_detectada);
                return wings.length > 0 ? Math.max(...wings) : 0;
            }}

            // Ordenar
            if (currentSort === 'price-asc') {{
                filtered.sort((a, b) => getCleanPrice(a) - getCleanPrice(b));
            }} else if (currentSort === 'price-desc') {{
                filtered.sort((a, b) => getCleanPrice(b) - getCleanPrice(a));
            }} else if (currentSort === 'liters-asc') {{
                filtered.sort((a, b) => getMaxLiters(a) - getMaxLiters(b));
            }} else if (currentSort === 'liters-desc') {{
                filtered.sort((a, b) => getMaxLiters(b) - getMaxLiters(a));
            }} else if (currentSort === 'wing-asc') {{
                filtered.sort((a, b) => getMaxWing(a) - getMaxWing(b));
            }} else if (currentSort === 'wing-desc') {{
                filtered.sort((a, b) => getMaxWing(b) - getMaxWing(a));
            }} else if (currentSort === 'date-desc') {{
                filtered.sort((a, b) => new Date(b.fecha_publicacion || 0) - new Date(a.fecha_publicacion || 0));
            }}

            if (filtered.length === 0) {{
                grid.innerHTML = `<div class="no-results">No se encontraron anuncios que coincidan con la búsqueda o filtros.</div>`;
                return;
            }}

            filtered.forEach(item => {{
                const itemIsHighlighted = isHighlighted(item);
                const card = document.createElement('div');
                card.className = 'card' + (itemIsHighlighted ? ' match' : '');

                // Botones de acción (Favoritos/Descartes)
                const isFav = favorites.includes(item.url);
                const isDismissed = dismissed.includes(item.url);
                
                const favBtnHtml = `<button class="action-btn fav${{isFav ? ' active' : ''}}" onclick="toggleFav('${{item.url}}', event)" title="${{isFav ? 'Quitar de favoritos' : 'Marcar como favorito'}}">★</button>`;
                
                let dismissBtnHtml = '';
                if (currentFilter === 'dismissed') {{
                    dismissBtnHtml = `<button class="action-btn dismiss" onclick="restoreItem('${{item.url}}', event)" title="Recuperar anuncio">🔄</button>`;
                }} else {{
                    dismissBtnHtml = `<button class="action-btn dismiss" onclick="dismissItem('${{item.url}}', event)" title="Descartar anuncio">✕</button>`;
                }}

                const actionsHtml = `
                    <div class="card-actions">
                        ${{favBtnHtml}}
                        ${{dismissBtnHtml}}
                    </div>
                `;

                // Badges
                let badgesHtml = '';
                if (itemIsHighlighted) {{
                    badgesHtml += `<span class="badge badge-criteria">${{hasActiveCriteria ? '¡Cumple Filtro!' : '¡Cumple Criterios!'}}</span>`;
                }}
                
                const statusValue = item.estado || 'Disponible';
                const statusClass = statusValue.toLowerCase();
                badgesHtml += `<span class="badge badge-status ${{statusClass}}">${{statusValue}}</span>`;

                const isShippable = item.envio === 'Envío disponible';
                badgesHtml += `<span class="badge badge-shipping ${{isShippable ? '' : 'no'}}">${{item.envio}}</span>`;

                if (item.litros_detectados && item.litros_detectados !== '[]') {{
                    badgesHtml += `<span class="badge badge-info">${{item.litros_detectados}} Litros</span>`;
                }}
                if (item.ala_detectada && item.ala_detectada !== '[]') {{
                    badgesHtml += `<span class="badge badge-info">${{item.ala_detectada}}m Ala</span>`;
                }}

                const imgs = item.imagen ? item.imagen.split('|') : [];
                let imageHtml = '';
                if (imgs.length > 0 && imgs[0] !== '') {{
                    const badgePhotos = imgs.length > 1 ? `<span class="badge-photos-count">📸 ${{imgs.length}} fotos</span>` : '';
                    const itemIndex = listings.indexOf(item);
                    imageHtml = `
                        <div class="card-image-container" onclick="openGallery(${{itemIndex}}, 0)">
                            <img src="${{imgs[0]}}" class="card-image" alt="${{item.titulo}}" loading="lazy">
                            ${{badgePhotos}}
                            <div class="card-image-overlay">Ampliar galería</div>
                        </div>
                    `;
                }} else {{
                    imageHtml = `<div class="card-image-placeholder">Sin imagen de producto</div>`;
                }}

                card.innerHTML = `
                    ${{actionsHtml}}
                    ${{imageHtml}}
                    <div class="card-body">
                        <div>
                            <div class="card-header">
                                <span class="card-title" title="${{item.titulo}}">${{item.titulo}}</span>
                                <span class="card-price">${{item.precio ? item.precio + '€' : 'N/D'}}</span>
                            </div>
                            <div class="card-meta">
                                <span>📍 ${{item.ubicacion}}</span>
                                <span>📅 ${{item.fecha_publicacion}}</span>
                            </div>
                            <div class="card-badges">
                                ${{badgesHtml}}
                            </div>
                            <div class="card-description" onclick="this.classList.toggle('expanded')" title="Haz clic para expandir la descripción">
                                ${{item.descripcion}}
                            </div>
                        </div>
                        <div class="card-footer">
                            <a href="${{item.url}}" target="_blank" class="btn-link">Ver en Wallapop</a>
                        </div>
                    </div>
                `;
                grid.appendChild(card);
            }});
        }}

        // Listeners para botones de filtrado principales
        document.querySelectorAll('.controls .filter-group:first-of-type .filter-btn').forEach(btn => {{
            btn.addEventListener('click', (e) => {{
                document.querySelectorAll('.controls .filter-group:first-of-type .filter-btn').forEach(b => b.classList.remove('active'));
                e.target.classList.add('active');
                currentFilter = e.target.getAttribute('data-filter');
                renderListings();
            }});
        }});

        // Listeners para filtros de categoría
        document.querySelectorAll('#category-filter-group .filter-btn').forEach(btn => {{
            btn.addEventListener('click', (e) => {{
                document.querySelectorAll('#category-filter-group .filter-btn').forEach(b => b.classList.remove('active'));
                e.target.classList.add('active');
                currentCategory = e.target.getAttribute('data-category');
                renderListings();
            }});
        }});

        // Listener para caja de búsqueda
        document.getElementById('search-input').addEventListener('input', (e) => {{
            searchQuery = e.target.value;
            renderListings();
        }});

        // Listeners para filtros numéricos (litros, vela y precios)
        document.getElementById('liters-min').addEventListener('input', () => renderListings());
        document.getElementById('liters-max').addEventListener('input', () => renderListings());
        document.getElementById('wing-min').addEventListener('input', () => renderListings());
        document.getElementById('wing-max').addEventListener('input', () => renderListings());
        document.getElementById('price-min-filter').addEventListener('input', () => renderListings());
        document.getElementById('price-max-filter').addEventListener('input', () => renderListings());

        // Listener para selector de orden
        document.getElementById('sort-select').addEventListener('change', (e) => {{
            currentSort = e.target.value;
            renderListings();
        }});

        // --- Gallery Lightbox Modal Functions ---
        let activeGalleryItem = null;
        let activeImageIndex = 0;

        window.openGallery = function(itemIndex, imgIndex) {{
            activeGalleryItem = listings[itemIndex];
            activeImageIndex = imgIndex;
            
            const modal = document.getElementById('gallery-modal');
            modal.style.display = 'flex';
            updateGalleryContent();
            
            document.addEventListener('keydown', handleKeyNavigation);
        }};

        window.closeGallery = function() {{
            const modal = document.getElementById('gallery-modal');
            modal.style.display = 'none';
            document.removeEventListener('keydown', handleKeyNavigation);
        }};

        window.closeGalleryOnOutsideClick = function(event) {{
            const modal = document.getElementById('gallery-modal');
            if (event.target === modal) {{
                closeGallery();
            }}
        }};

        function updateGalleryContent() {{
            if (!activeGalleryItem) return;
            const imgs = activeGalleryItem.imagen ? activeGalleryItem.imagen.split('|') : [];
            if (imgs.length === 0) return;
            
            if (activeImageIndex < 0) activeImageIndex = imgs.length - 1;
            if (activeImageIndex >= imgs.length) activeImageIndex = 0;
            
            const imgEl = document.getElementById('modal-image');
            imgEl.src = imgs[activeImageIndex];
            
            const infoEl = document.getElementById('modal-info');
            infoEl.innerText = `${{activeGalleryItem.titulo}} (${{activeImageIndex + 1}} de ${{imgs.length}})`;
            
            const thumbsEl = document.getElementById('modal-thumbnails');
            thumbsEl.innerHTML = '';
            
            if (imgs.length > 1) {{
                imgs.forEach((imgSrc, idx) => {{
                    const thumb = document.createElement('img');
                    thumb.src = imgSrc;
                    thumb.className = `thumb-img${{idx === activeImageIndex ? ' active' : ''}}`;
                    thumb.onclick = () => {{
                        activeImageIndex = idx;
                        updateGalleryContent();
                    }};
                    thumbsEl.appendChild(thumb);
                }});
            }}
        }}

        window.prevImage = function() {{
            activeImageIndex--;
            updateGalleryContent();
        }};

        window.nextImage = function() {{
            activeImageIndex++;
            updateGalleryContent();
        }};

        function handleKeyNavigation(e) {{
            if (e.key === 'ArrowLeft') {{
                prevImage();
            }} else if (e.key === 'ArrowRight') {{
                nextImage();
            }} else if (e.key === 'Escape') {{
                closeGallery();
            }}
        }}

        // Render inicial
        renderListings();

        // Colapsar filtros por defecto en móviles al cargar
        if (window.innerWidth < 768) {{
            const container = document.getElementById('filters-container');
            const btn = document.getElementById('toggle-filters-btn');
            container.style.maxHeight = '0px';
            container.style.opacity = '0';
            btn.innerText = 'Mostrar Filtros';
        }}
    </script>

    <!-- Modal para visualización de fotos -->
    <div id="gallery-modal" class="modal" onclick="closeGalleryOnOutsideClick(event)">
        <div class="modal-content-wrapper">
            <span class="close-btn" onclick="closeGallery()">&times;</span>
            <button class="nav-btn prev-btn" onclick="prevImage()">&lsaquo;</button>
            <img id="modal-image" class="modal-img" src="" alt="Vista completa">
            <button class="nav-btn next-btn" onclick="nextImage()">&rsaquo;</button>
            <div id="modal-info" class="modal-info"></div>
            <div id="modal-thumbnails" class="thumbnails-container"></div>
        </div>
    </div>
</body>
</html>
"""
    try:
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
    except Exception as e:
        logger.error(f"Error escribiendo archivo HTML de reporte: {e}")


if __name__ == "__main__":
    main()
