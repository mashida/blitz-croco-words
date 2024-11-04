from pyaspeller import YandexSpeller

if __name__ == "__main__":
    speller = YandexSpeller()
    result = speller.spelled('Колакал малако')
    print(result)
