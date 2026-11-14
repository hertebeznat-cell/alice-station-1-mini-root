// SPDX-License-Identifier: MIT
// Protocol based on pyamlboot (BayLibre SAS, Neil Armstrong) and Station Mini Root (TrueKris and contributors).
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/IOCFPlugIn.h>
#include <IOKit/usb/IOUSBLib.h>

#include <CommonCrypto/CommonDigest.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

typedef IOUSBDeviceInterface320 **Device;
typedef IOUSBInterfaceInterface300 **Interface;
enum { SystemSize = 80 * 1024 * 1024 };

static io_service_t wait_for_device(void) {
    uint64_t ignored_id = 0;
    for (int attempt = 0; attempt < 6000; ++attempt) {
        CFMutableDictionaryRef matching = IOServiceMatching("IOUSBHostDevice");
        int vendor = 0x1b8e, product = 0xc003;
        CFNumberRef vendor_number = CFNumberCreate(NULL, kCFNumberIntType, &vendor);
        CFNumberRef product_number = CFNumberCreate(NULL, kCFNumberIntType, &product);
        CFDictionarySetValue(matching, CFSTR("idVendor"), vendor_number);
        CFDictionarySetValue(matching, CFSTR("idProduct"), product_number);
        CFRelease(vendor_number);
        CFRelease(product_number);
        io_service_t service = IOServiceGetMatchingService(kIOMainPortDefault, matching);
        if (service) {
            uint64_t id = 0;
            IORegistryEntryGetRegistryEntryID(service, &id);
            if (attempt == 0) ignored_id = id;
            if (id != ignored_id) return service;
            IOObjectRelease(service);
        }
        usleep(25000);
    }
    return IO_OBJECT_NULL;
}

static int check(IOReturn result, const char *operation) {
    if (result == kIOReturnSuccess) return 1;
    fprintf(stderr, "%s: 0x%08x\n", operation, result);
    return 0;
}

static void *query(io_service_t service, CFUUIDRef plugin_type, CFUUIDRef interface_id) {
    IOCFPlugInInterface **plugin = NULL;
    SInt32 score;
    if (!check(IOCreatePlugInInterfaceForService(service, plugin_type, kIOCFPlugInInterfaceID,
                                                 &plugin, &score), "IOCreatePlugInInterface"))
        return NULL;
    void *interface = NULL;
    HRESULT result = (*plugin)->QueryInterface(plugin, CFUUIDGetUUIDBytes(interface_id), &interface);
    IODestroyPlugInInterface(plugin);
    if (result != S_OK) fprintf(stderr, "QueryInterface: 0x%08x\n", (unsigned)result);
    return result == S_OK ? interface : NULL;
}

static int transfer(Device device, UInt8 type, UInt8 code, UInt16 value, UInt16 index,
                    void *data, UInt16 size, UInt32 timeout, UInt32 *actual) {
    IOUSBDevRequestTO request = {
        .bmRequestType = type, .bRequest = code, .wValue = value, .wIndex = index,
        .wLength = size, .pData = data, .noDataTimeout = timeout, .completionTimeout = timeout
    };
    int success = check((*device)->DeviceRequestTO(device, &request), "USB control transfer");
    *actual = request.wLenDone;
    return success;
}

static int control(Device device, UInt8 type, UInt8 code, UInt16 index, void *data, UInt16 size) {
    UInt32 actual;
    return transfer(device, type, code, 0, index, data, size, 5000, &actual) && actual == size;
}

static Interface open_interface(Device device, UInt8 *input_pipe, UInt8 *output_pipe) {
    IOUSBFindInterfaceRequest request = { kIOUSBFindInterfaceDontCare, kIOUSBFindInterfaceDontCare,
        kIOUSBFindInterfaceDontCare, kIOUSBFindInterfaceDontCare };
    io_iterator_t iterator = IO_OBJECT_NULL;
    if (!check((*device)->CreateInterfaceIterator(device, &request, &iterator), "Interface iterator"))
        return NULL;
    io_service_t service = IOIteratorNext(iterator);
    IOObjectRelease(iterator);
    if (!service) {
        fputs("USB-интерфейс не найден.\n", stderr);
        return NULL;
    }
    Interface interface = query(service, kIOUSBInterfaceUserClientTypeID, kIOUSBInterfaceInterfaceID300);
    IOObjectRelease(service);
    if (!interface) return NULL;
    if (check((*interface)->USBInterfaceOpen(interface), "USBInterfaceOpen")) {
        UInt8 count = 0;
        if (check((*interface)->GetNumEndpoints(interface, &count), "GetNumEndpoints")) {
            for (UInt8 pipe = 1; pipe <= count; ++pipe) {
                UInt8 direction, number, type, interval;
                UInt16 packet_size;
                if (!check((*interface)->GetPipeProperties(interface, pipe, &direction, &number,
                          &type, &packet_size, &interval), "GetPipeProperties")) continue;
                if (type == kUSBBulk && direction == kUSBIn && number == 1) *input_pipe = pipe;
                if (type == kUSBBulk && direction == kUSBOut) *output_pipe = pipe;
            }
        }
        if (*input_pipe && *output_pipe) return interface;
        fputs("Не найдены USB-каналы чтения и записи.\n", stderr);
        (*interface)->USBInterfaceClose(interface);
    }
    (*interface)->Release(interface);
    return NULL;
}

static int send_command(Device device, char *command) {
    return control(device, 0x40, 0x34, 2, command, (UInt16)(strlen(command) + 1));
}

static int receive_success(Interface interface, UInt8 pipe, const char *command) {
    time_t deadline = time(NULL) + 60;
    do {
        char response[513] = {0};
        UInt32 length = 512;
        if (!check((*interface)->ReadPipeTO(interface, pipe, response, &length, 60000, 60000), command))
            return 0;
        if (strcmp(response, "success") == 0) return 1;
        if (strncmp(response, "Continue:34", 11) != 0) {
            fprintf(stderr, "%s: неожиданный ответ %s\n", command, response);
            return 0;
        }
        usleep(100000);
    } while (time(NULL) < deadline);
    fprintf(stderr, "%s: истекло время ожидания.\n", command);
    return 0;
}

static int read_system(Device device, Interface interface, UInt8 pipe, FILE *backup, unsigned char digest[CC_SHA256_DIGEST_LENGTH]) {
    if (!send_command(device, "upload store system normal 0x5000000")
        || !receive_success(interface, pipe, "upload system")) return 0;

    CC_SHA256_CTX hash;
    CC_SHA256_Init(&hash);
    for (UInt32 received = 0; received < SystemSize;) {
        UInt32 header[4];
        if (!control(device, 0xc0, 0x33, 0, header, sizeof(header))) return 0;
        UInt32 length = header[1];
        if (header[0] != 0xefe8 || !length || length > 0x10000 || length > SystemSize - received) {
            fprintf(stderr, "Некорректный блок при %u байтах: %x %u %u %x\n",
                    received, header[0], length, header[2], header[3]);
            return 0;
        }
        for (UInt32 offset = 0; offset < length;) {
            unsigned char data[4096];
            UInt32 size = length - offset < sizeof(data) ? length - offset : sizeof(data);
            if (!check((*interface)->ReadPipeTO(interface, pipe, data, &size, 5000, 5000), "Чтение system")
                || !size || size > length - offset) return 0;
            if (backup && fwrite(data, 1, size, backup) != size) {
                perror("Сохранение бэкапа");
                return 0;
            }
            CC_SHA256_Update(&hash, data, size);
            offset += size;
        }
        UInt32 before = received;
        received += length;
        if (before / (8 * 1024 * 1024) != received / (8 * 1024 * 1024))
            printf("Прочитано %u из 80 МиБ\n", received / (1024 * 1024));
    }
    CC_SHA256_Final(digest, &hash);
    return 1;
}

enum { BlockSize = 0x10000 };

static int write_system(Device device, Interface interface, UInt8 input, UInt8 output,
                        const UInt32 *image) {
    char target[] = "download store system normal 0x5000000";
    char status[65] = {0};
    UInt32 actual;
    if (!send_command(device, "disk_initial 0")
        || !receive_success(interface, input, "disk_initial")
        || !control(device, 0x40, 0x30, 1, target, sizeof(target))
        || !transfer(device, 0xc0, 0x31, 0, 0, status, 64, 60000, &actual)) return 0;
    if (strcmp(status, "success") != 0) {
        fprintf(stderr, "Загрузчик отклонил запись: %s\n", status);
        return 0;
    }

    for (UInt32 sequence = 0; sequence < SystemSize / BlockSize; ++sequence) {
        const UInt32 *block = image + sequence * (BlockSize / sizeof(UInt32));
        UInt32 checksum = 0;
        for (size_t word = 0; word < BlockSize / sizeof(UInt32); ++word) checksum += block[word];
        UInt32 header[8] = {0, BlockSize, sequence, checksum, 0x020000ef, 0, 0, 0};
        if (!transfer(device, 0x40, 0x32, 1, 0xffff, header, sizeof(header), 5000, &actual)
            || actual != sizeof(header)
            || !check((*interface)->WritePipeTO(interface, output, (void *)block, BlockSize, 15000, 15000),
                      "Отправка блока")) {
            fprintf(stderr, "Ошибка записи блока %u\n", sequence);
            return 0;
        }
        time_t deadline = time(NULL) + 60;
        for (;;) {
            char ack[513] = {0};
            UInt32 size = 512;
            if (!check((*interface)->ReadPipeTO(interface, input, ack, &size, 60000, 60000),
                       "Подтверждение блока")) return 0;
            if (strncmp(ack, "OK!!", 4) == 0) break;
            if (strncmp(ack, "Continue:32", 11) != 0 || time(NULL) >= deadline) {
                fprintf(stderr, "Блок %u: неожиданный ответ %s\n", sequence, ack);
                return 0;
            }
        }
        if ((sequence + 1) % 128 == 0) printf("Записано %u из 80 МиБ\n", (sequence + 1) / 16);
    }
    return send_command(device, "download get_status")
        && receive_success(interface, input, "download get_status");
}

static void print_digest(const unsigned char *digest) {
    for (size_t i = 0; i < CC_SHA256_DIGEST_LENGTH; ++i) printf("%02x", digest[i]);
    puts("");
}

static int identify(Device device) {
    unsigned char reply[8];
    if (!control(device, 0xc0, 0x20, 0, reply, sizeof(reply))) return 0;
    printf("IDENTIFY:");
    for (size_t i = 0; i < sizeof(reply); ++i) printf(" %02x", reply[i]);
    puts("");
    return reply[3] == 0x10 && reply[4] == 0;
}

static int parse_digest(const char *hex, unsigned char *out) {
    if (strlen(hex) != 2 * CC_SHA256_DIGEST_LENGTH) return 0;
    const char digits[] = "0123456789abcdef";
    for (size_t i = 0; i < CC_SHA256_DIGEST_LENGTH; ++i) {
        const char *high = strchr(digits, hex[2 * i]);
        const char *low = strchr(digits, hex[2 * i + 1]);
        if (!high || !low) return 0;
        out[i] = (unsigned char)((high - digits) * 16 + (low - digits));
    }
    return 1;
}

static int flash(Device device, Interface interface, UInt8 input, UInt8 output, const UInt32 *image,
                 const unsigned char *before, const unsigned char *after, const char *snapshot) {
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    puts("Проверяю текущий раздел system.");
    FILE *current = NULL;
    if (snapshot && !(current = fopen(snapshot, "wbx"))) {
        perror("Создание копии текущего system");
        return 1;
    }
    int read_ok = read_system(device, interface, input, current, digest);
    if (current) {
        if (fflush(current) != 0 || fsync(fileno(current)) != 0) read_ok = 0;
        if (fclose(current) != 0) read_ok = 0;
        if (!read_ok) unlink(snapshot);
    }
    if (!read_ok) return 1;
    print_digest(digest);
    if (memcmp(digest, after, sizeof(digest)) == 0) {
        puts("Целевой образ уже на колонке.");
    } else {
        if (!snapshot && memcmp(digest, before, sizeof(digest)) != 0) {
            fputs("Текущий system не совпадает с ожидаемым SHA-256. Запись отменена.\n", stderr);
            return 1;
        }
        if (!identify(device)) return 1;
        puts("Записываю system. Не отключайте питание и USB.");
        if (!write_system(device, interface, input, output, image)) return 3;
        puts("Читаю записанный раздел обратно.");
        if (!read_system(device, interface, input, NULL, digest)) return 3;
        print_digest(digest);
        if (memcmp(digest, after, sizeof(digest)) != 0) return 3;
    }
    puts("SHA-256 совпал. Отправляю команду перезагрузки.");
    if (!send_command(device, "reset"))
        puts("Образ проверен. Проверьте, перезагрузилась ли колонка.");
    return 0;
}

static int save_backup(Device device, Interface interface, UInt8 input, const char *path) {
    FILE *file = fopen(path, "wbx");
    if (!file) {
        perror("Создание бэкапа");
        return 1;
    }
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    int success = read_system(device, interface, input, file, digest);
    if (fflush(file) != 0 || fsync(fileno(file)) != 0) success = 0;
    if (fclose(file) != 0) success = 0;
    if (success) print_digest(digest);
    else unlink(path);
    return success ? 0 : 1;
}

int main(int argc, char **argv) {
    int probe = argc == 2 && strcmp(argv[1], "probe") == 0;
    int backup = argc == 3 && strcmp(argv[1], "backup") == 0;
    int restore = argc == 5 && strcmp(argv[1], "restore") == 0;
    int write = restore || (argc == 5 && strcmp(argv[1], "flash") == 0);
    if (!probe && !backup && !write) {
        fputs("Usage: mini-usb probe | backup FILE | flash FILE CURRENT_SHA256 IMAGE_SHA256 | restore FILE SNAPSHOT IMAGE_SHA256\n", stderr);
        return 2;
    }

    setvbuf(stdout, NULL, _IONBF, 0);
    UInt32 *image = NULL;
    unsigned char before[CC_SHA256_DIGEST_LENGTH], after[CC_SHA256_DIGEST_LENGTH];
    if (write) {
        if ((!restore && !parse_digest(argv[3], before)) || !parse_digest(argv[4], after)) {
            fputs("Некорректный SHA-256.\n", stderr);
            return 2;
        }
        FILE *file = fopen(argv[2], "rb");
        if (!file) {
            perror("Открытие образа");
            return 1;
        }
        image = malloc(SystemSize);
        int loaded = image && fread(image, 1, SystemSize, file) == SystemSize
            && fgetc(file) == EOF && !ferror(file);
        fclose(file);
        unsigned char digest[CC_SHA256_DIGEST_LENGTH];
        if (!loaded || !CC_SHA256(image, SystemSize, digest) || memcmp(digest, after, sizeof(digest))) {
            fputs("Неверный размер или SHA-256 образа.\n", stderr);
            free(image);
            return 1;
        }
    }

    puts("Переподключите USB-кабель к разблокированному Mac. Кнопки зажимать не нужно.");
    io_service_t service = wait_for_device();
    if (!service) {
        fputs("Загрузчик 1b8e:c003 не появился.\n", stderr);
        free(image);
        return 1;
    }
    Device device = query(service, kIOUSBDeviceUserClientTypeID, kIOUSBDeviceInterfaceID320);
    IOObjectRelease(service);
    if (!device) {
        free(image);
        return 1;
    }
    int opened = check((*device)->USBDeviceOpen(device), "USBDeviceOpen");
    int result = 1;
    if (opened && identify(device)) {
        if (probe) {
            result = 0;
        } else {
            UInt8 input = 0, output = 0;
            Interface interface = open_interface(device, &input, &output);
            if (interface) {
                if (send_command(device, "    low_power") && receive_success(interface, input, "low_power")) {
                    result = backup ? save_backup(device, interface, input, argv[2])
                                    : flash(device, interface, input, output, image, before, after, restore ? argv[3] : NULL);
                }
                (*interface)->USBInterfaceClose(interface);
                (*interface)->Release(interface);
            }
        }
    }
    if (result == 3)
        fputs("Проверка после начала записи не прошла. Перезагрузка не запрошена. Сохраните питание и USB.\n", stderr);
    if (opened) (*device)->USBDeviceClose(device);
    (*device)->Release(device);
    free(image);
    return result;
}
