from setuptools import setup, find_packages

try:
    from pypandoc import convert
    def read_markdown(file: str) -> str:
        return convert(file, "rst")
except ImportError:
    def read_markdown(file: str) -> str:
        return open(file, "r").read()

setup(
    name="yatadis",
    version="0.4.1",
    packages=find_packages(exclude=["tests"]),
    install_requires=open("requirements.txt", "r").readlines(),
    url="https://github.com/wtsi-hgi/yatadis",
    license="GPL3",
    description="Yet Another Terraform Ansible Dynamic Inventory Script",
    long_description=read_markdown("README.md"),
    entry_points={
        "console_scripts": [
            "yatadis=yatadis.yatadis:main"
        ]
    }
)
