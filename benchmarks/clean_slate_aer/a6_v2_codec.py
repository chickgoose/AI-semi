#!/usr/bin/env python3
"""Bit-exact non-expanding block codec model for A6 v2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


BLOCK_SIZE = 16


@dataclass(frozen=True)
class Block:
    bits: str
    mode: str
    event_count: int


def _token_payload(addresses: list[int], previous: int | None) -> str:
    parts = ["0"]  # compressed subtype
    for address in addresses:
        if previous == address:
            parts.append("0")
        elif previous is not None and previous < 15 and address == previous + 1:
            parts.append("110")
        elif previous is not None and previous > 0 and address == previous - 1:
            parts.append("111")
        else:
            parts.append("101" + format(address, "04b"))
        previous = address
    return "".join(parts)


def _dictionary_payload(addresses: list[int], block_size: int) -> str | None:
    if len(addresses) != block_size:
        return None
    dictionary = list(dict.fromkeys(addresses))
    index_width = (len(dictionary) - 1).bit_length()
    mapping = {address: index for index, address in enumerate(dictionary)}
    parts = ["1", format(len(dictionary) - 1, "04b")]
    parts.extend(format(address, "04b") for address in dictionary)
    if index_width:
        parts.extend(format(mapping[address], f"0{index_width}b") for address in addresses)
    return "".join(parts)


def _frame_compressed(payload: str) -> str:
    padding = (1 - (len(payload) + 2)) % 4
    return payload + ("0" * padding) + format(padding, "02b")


def encode_block(
    addresses: Iterable[int],
    previous: int | None = None,
    block_size: int = BLOCK_SIZE,
) -> Block:
    values = list(addresses)
    if not 1 <= len(values) <= block_size:
        raise ValueError("block exceeds configured size")
    if any(not isinstance(address, int) or not 0 <= address < 16 for address in values):
        raise ValueError("address outside four-bit range")
    raw = "".join(format(address, "04b") for address in values)
    candidates = [(_frame_compressed(_token_payload(values, previous)), "token")]
    dictionary = _dictionary_payload(values, block_size)
    if dictionary is not None:
        candidates.append((_frame_compressed(dictionary), "dictionary"))
    bits, mode = min(candidates, key=lambda item: len(item[0]))
    if len(bits) >= len(raw):
        bits, mode = raw, "raw"
    assert len(bits) <= 4 * len(values)
    return Block(bits, mode, len(values))


def decode_block(block: Block, previous: int | None = None) -> list[int]:
    bits = block.bits
    if len(bits) > 4 * block.event_count:
        raise ValueError("block exceeds raw bound")
    if len(bits) % 4 == 0:
        if len(bits) != 4 * block.event_count:
            raise ValueError("raw length/count mismatch")
        return [int(bits[start:start + 4], 2) for start in range(0, len(bits), 4)]
    if len(bits) % 4 != 1 or len(bits) < 3:
        raise ValueError("illegal framed length")
    padding = int(bits[-2:], 2)
    if padding and bits[-2-padding:-2] != "0" * padding:
        raise ValueError("nonzero compressed padding")
    payload = bits[:len(bits) - padding - 2]
    if not payload:
        raise ValueError("empty compressed payload")

    if payload[0] == "1":
        if block.event_count != BLOCK_SIZE or len(payload) < 5:
            raise ValueError("dictionary requires a full block")
        count = int(payload[1:5], 2) + 1
        cursor = 5
        if len(payload) < cursor + 4 * count:
            raise ValueError("truncated dictionary")
        dictionary = [int(payload[cursor + 4*i:cursor + 4*i + 4], 2)
                      for i in range(count)]
        cursor += 4 * count
        index_width = (count - 1).bit_length()
        if len(payload) != cursor + BLOCK_SIZE * index_width:
            raise ValueError("dictionary index length mismatch")
        if index_width == 0:
            return [dictionary[0]] * BLOCK_SIZE
        output = []
        for _ in range(BLOCK_SIZE):
            index = int(payload[cursor:cursor + index_width], 2)
            cursor += index_width
            if index >= count:
                raise ValueError("dictionary index out of range")
            output.append(dictionary[index])
        return output

    output = []
    cursor = 1
    while cursor < len(payload):
        if payload[cursor] == "0":
            if previous is None:
                raise ValueError("SAME without history")
            output.append(previous)
            cursor += 1
        else:
            if cursor + 3 > len(payload):
                raise ValueError("truncated token")
            prefix = payload[cursor:cursor + 3]
            if prefix == "101":
                if cursor + 7 > len(payload):
                    raise ValueError("truncated RAW token")
                previous = int(payload[cursor + 3:cursor + 7], 2)
                cursor += 7
            elif prefix == "110":
                if previous is None or previous == 15:
                    raise ValueError("illegal DELTA+1")
                previous += 1
                cursor += 3
            elif prefix == "111":
                if previous is None or previous == 0:
                    raise ValueError("illegal DELTA-1")
                previous -= 1
                cursor += 3
            else:
                raise ValueError("reserved token")
            output.append(previous)
        if len(output) > block.event_count:
            raise ValueError("too many decoded events")
    if len(output) != block.event_count:
        raise ValueError("decoded event-count mismatch")
    return output


def encode(addresses: Iterable[int]) -> list[Block]:
    values = list(addresses)
    blocks = []
    previous = None
    for start in range(0, len(values), BLOCK_SIZE):
        part = values[start:start + BLOCK_SIZE]
        block = encode_block(part, previous)
        blocks.append(block)
        previous = part[-1]
    return blocks


def decode(blocks: Iterable[Block]) -> list[int]:
    output = []
    previous = None
    for block in blocks:
        values = decode_block(block, previous)
        output.extend(values)
        previous = values[-1]
    return output
