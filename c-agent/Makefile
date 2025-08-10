#this part do that
# Makefile pour construire l'agent C

#this other part do that
# variables de compilation
CC := gcc
CFLAGS := -O2 -g -std=c11 -Wall -Wextra -Wpedantic $(shell pkg-config --cflags libwebsockets jansson libcurl)
LDFLAGS := $(shell pkg-config --libs libwebsockets jansson libcurl)
PREFIX ?= /usr/local
BINARY := c-agent
SRC := src/main.c
OBJ := $(SRC:.c=.o)

#this other part do that
# règles principales
all: $(BINARY)

$(BINARY): $(OBJ)
	$(CC) $(CFLAGS) -o $@ $^ $(LDFLAGS)

%.o: %.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -c -o $@ $<

#this other part do that
# installation système
install: $(BINARY)
	install -d $(DESTDIR)$(PREFIX)/bin
	install -m 0755 $(BINARY) $(DESTDIR)$(PREFIX)/bin/$(BINARY)

#this other part do that
# nettoyage
clean:
	rm -f $(OBJ) $(BINARY)

.PHONY: all install clean