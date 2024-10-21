import os


def cur():
    print(os.path.dirname(os.path.realpath(__file__)))

if __name__ == '__main__':
    cur()