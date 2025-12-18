import requests
import warnings
warnings.filterwarnings('ignore')
from bs4 import BeautifulSoup
from requests_html import HTMLSession
import re
import keyboard
import threading
import time

# Minimum seller rating to save seller
MIN_RATING =  1000

# Phrases not found in the product description
not_phrases = ["laser", "prop", "replica", "model"]

url = "https://www.ebay.co.uk/sch/i.html?_nkw=keyboard"

headers = {
    'User-Agent': 'Mozilla/5.0'
}
sellers = []

proxy = {
  "http": "http://brd-customer-hl_c32170ea-zone-residential_proxy1:3jl9fzirr9k1@brd.superproxy.io:33335",
  "https": "http://brd-customer-hl_c32170ea-zone-residential_proxy1:3jl9fzirr9k1@brd.superproxy.io:33335"
}
session = HTMLSession()
session.proxies.update(proxy)
# sellers which have been searched
complete_sellers = []

pending_searches = []

# Products which have been searched
complete_searches = {}


# Gets sellers for a specific search
def search(search):
    print("searching for: " + search)
    complete_searches[search] = 0
    url = search
    response = requests.get(url, headers=headers)

    html = response.text

    soup = BeautifulSoup(html, 'html.parser')
    
    for item in soup.select('.s-item')[2:]:
        title = item.select_one('.s-item__title')
        price = item.select_one('.s-item__price')
        seller_info = item.select_one('.s-item__seller-info-text')
        product_link = item.select_one('.s-item__link')
        product_url = product_link['href']

        if seller_info is not None:
            rating = seller_info.text.split(' ')[1]
            rating = rating.replace("(", "").replace(")", "").replace(",", "")
            rating = int(rating)
        else:
            rating = 0

        # Checks listing exists
        if title and price and seller_info and rating > MIN_RATING and contains(product_url, "3d printed"):

            
            seller = seller_info.text.split(' ')[0]
           
            if seller not in sellers and seller not in complete_sellers:
                sellers.append(seller)
                complete_searches[search] += 1
    print(str(complete_searches[search]) + " sellers found")


            

# Gets products for a paricular seller
def seller_search(seller):
    print("Finding new listings from " + seller)

    #Link to seller page
    url = "https://www.ebay.co.uk/sch/i.html?_dkr=1&iconV2Request=true&_blrs=recall_filtering&_ssn=" + seller
    
    response = requests.get(url, headers=headers)
    html = response.text

    soup = BeautifulSoup(html, 'html.parser')

 
    for item in soup.select('.s-item')[2:]:
        title = item.select_one('.s-item__title')
        price = item.select_one('.s-item__price')
        product_link = item.select_one('.s-item__link')
        product_url = product_link['href']

        # Creates new product, if contains key word and above given sale count
        # sold = sales(product_url)

        sold = item.select_one('.s-item__dynamic.s-item__quantitySold')
        if sold:
         sold = int(sold.text.split()[0].replace("+", "").replace(",", ""))
        else:
            sold = 0 

        if title and price and sold >= 0 and contains(product_url, "3d printed"):
            pending_searches.append(title.text)
            with open("data.txt", "a") as f:
                f.write(str(sold) + "  " + product_url + "\n")
    
    print(str(len(pending_searches)) + " listings found")



# Gets the link for a particular search
def search_link(text):
    text = text.replace(' ', '+')
    text = text.replace('/', '%2F')
    text = text.replace(',', '%2C')
    text = 'https://www.ebay.co.uk/sch/i.html?_nkw=' + text
    return text



 
# Checks product description contains given phrase (not case-sensitive)
def contains(url, phrase):
    id = get_product_id(url)

    # Link to product description
    desc = "https://itm.ebaydesc.com/itmdesc/" + id

    response = requests.get(desc, headers=headers)
    html = response.text
    soup = BeautifulSoup(html, 'html.parser')

    # Gets visible text
    text = soup.get_text(separator=' ', strip=True).lower()

    for item in not_phrases:
        if item.lower() in text:
            return False

    # Checks for phrase in text
    if phrase.lower() in text:
        return True
    else:
        return False
    
# Returns product id, extracted from given URL
def get_product_id(url):
   match = re.search(r"/itm/(\d+)", url)
   product_id = match.group(1) 
   return product_id


pending_searches.append("BT Smart Hub 2 Wall mount bracket smarthub homehub Slim Custom - Internet Router")
pending_searches.append("oral b toothbrush stand")
pending_searches.append("VWindow Security Safety Lock for Polyplastic Latches in Caravans Motorhomes")
pending_searches.append("oil filler cap removal key tool keychain 71117691446 compatible with BMW motorbike")
pending_searches.append("Ikea STYRBAR Remote Adapter - UK Light Switch Converter - Cover")

# Searches sellers for first 10 products
def run():
    with open("data.txt", "w") as f:
        f.write("")
    with open("sellers.txt", "w") as f:
        f.write("")
    for i in range(30000):
        while len(sellers) == 0:
            
            while(len(pending_searches) == 0):
                time.sleep(5)
            # Searches for new sellers
            search(search_link(pending_searches.pop(0)))


        complete_sellers.append(sellers[0])
    

        if(len(sellers) > 0):
            try:
                with open("sellers.txt", "a") as f:
                    f.write(sellers[0] + "\n")
                # Finds sellers products
                seller_search(sellers.pop(0))
            except:
                time.sleep(2)

    print(complete_sellers)
    print(sellers)
    print(complete_searches)




threading.Thread(target=run, daemon=True).start()
threading.Thread(target=run, daemon=True).start()
threading.Thread(target=run, daemon=True).start()
threading.Thread(target=run, daemon=True).start()
threading.Thread(target=run, daemon=True).start()
threading.Thread(target=run, daemon=True).start()
threading.Thread(target=run, daemon=True).start()
threading.Thread(target=run, daemon=True).start()
threading.Thread(target=run, daemon=True).start()
threading.Thread(target=run, daemon=True).start()
threading.Thread(target=run, daemon=True).start()
threading.Thread(target=run, daemon=True).start()
threading.Thread(target=run, daemon=True).start()
threading.Thread(target=run, daemon=True).start()

while True:
    if keyboard.is_pressed('q'):
        print("You pressed 'q'. Exiting...")
        print(sellers)
        break