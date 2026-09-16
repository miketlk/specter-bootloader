# SD-only closure of the pinned IDF FatFs; no raw flash or wear levelling.
set(FAT "$ENV{IDF_PATH}/components/fatfs")
idf_component_register(SRCS "${FAT}/diskio/diskio.c" "${FAT}/diskio/diskio_sdmmc.c"
  "${FAT}/src/ff.c" "${FAT}/src/ffunicode.c" "${FAT}/port/freertos/ffsystem.c"
  "${FAT}/vfs/vfs_fat.c"
  INCLUDE_DIRS "${FAT}/diskio" "${FAT}/src" "${FAT}/vfs"
  "$ENV{IDF_PATH}/components/wear_levelling/include"
  "$ENV{IDF_PATH}/components/esp_partition/include"
  REQUIRES sdmmc esp_driver_sdmmc esp_driver_sdspi
  PRIV_REQUIRES vfs esp_driver_gpio)
