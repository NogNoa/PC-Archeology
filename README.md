# PC-Archeology

A collection of tools and documentation for analyzing and manipulating vintage PC disk images, especially 5¼" floppy disk formats and FAT12 filesystems.

## Features

- **FAT12 Disk Image Handling:**  
  Read, extract, and write files to FAT12 disk images, including support for various historical formats.
- **CGA Graphics Utilities:**  
  Render and convert CGA and MDA graphics data to PNG images.
- **OMF Object File Parsing:**  
  Parse and analyze OMF (Object Module Format) files.

## Usage

### Disk Image Tool

Extract all files from a disk image:
```sh
python src/5¼'-disk.py extract path/to/disk.img
```

Create a new disk image from a folder:
```sh
python src/5¼'-disk.py create path/to/folder -f path/to/folder
```

### CGA Graphics Tool

Convert a CGA graphics file to PNG:
```sh
python src/CGA.py path/to/file.bin cg
```

### OMF Parser

Parse OMF object files in a directory:
```sh
python src/omf.py path/to/objfiles/
```

## Documentation

- [doc/fat id table.xml](doc/fat%20id%20table.xml): FAT ID reference table for various disk formats.
- [doc/img structure.txt](doc/img%20structure.txt): Notes on disk image layout and file mapping.

## Requirements

- Python 3.10+
- [Pillow](https://python-pillow.org/) (for image operations)

## License

This project is for educational and research purposes.