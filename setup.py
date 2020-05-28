from setuptools import setup

setup(
    name='tarpn',
    version='',
    packages=['tarpn'],
    url='',
    license='',
    author='David Arthur',
    author_email='mumrah@gmail.com',
    description='',
    entry_points={
             'console_scripts': [
                 'tarpn-serial-dump = tarpn.tools.serial_dump:main',
                 'tarpn-kiss-cat = tarpn.tools.kiss_cat:main',
                 'tarpn-packet-dump = tarpn.tools.packet_dump:main'
             ]},
    python_requires='>=3.7'
)
