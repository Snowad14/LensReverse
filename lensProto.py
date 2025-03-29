from enum import IntEnum, unique
import sys
import os
import io # Needed for skipping fields

class ProtoError(Exception):
    def __init__(self, msg):
        self.msg = msg

    def __str__(self):
        # More informative error message
        return f"ProtoError: {self.msg}"


@unique
class ProtoFieldType(IntEnum):
    VARINT = 0      # int32, int64, uint32, uint64, sint32, sint64, bool, enum
    INT64 = 1       # fixed64, sfixed64, double
    STRING = 2      # string, bytes, embedded messages, packed repeated fields
    GROUPSTART = 3  # deprecated
    GROUPEND = 4    # deprecated
    INT32 = 5       # fixed32, sfixed32, float
    # Removed ERROR1/ERROR2 as they are not standard wire types
    # ERROR1 = 6
    # ERROR2 = 7


class ProtoField:
    def __init__(self, idx, type, val):
        self.idx = idx
        self.type: ProtoFieldType = type # Add type hint
        self.val = val

    def isAsciiStr(self):
        if not isinstance(self.val, bytes):
            return False
        # A slightly more robust check for printable ASCII + common whitespace
        # You might prefer a try-except decode('utf-8') approach later
        try:
            decoded_str = self.val.decode('ascii')
            return all(0x20 <= ord(c) <= 0x7E or c in ('\n', '\r', '\t') for c in decoded_str)
        except UnicodeDecodeError:
            return False

    def __str__(self):
        if self.type in (ProtoFieldType.INT32, ProtoFieldType.INT64, ProtoFieldType.VARINT):
            return "%d(%s): %d" % (self.idx, self.type.name, self.val)
        elif self.type == ProtoFieldType.STRING:
            # Try decoding as UTF-8 first for better representation
            try:
                # Limit length for display?
                preview = self.val.decode('utf-8')
                # Check if it's printable-like or just arbitrary bytes
                is_printable_utf8 = all(c.isprintable() or c.isspace() for c in preview)
                if is_printable_utf8:
                     # Add quotes for clarity
                    return '%d(%s): "%s"' % (self.idx, self.type.name, preview)
                else:
                    # Fallback to hex if non-printable chars found
                    return '%d(%s): h"%s"' % (self.idx, self.type.name, self.val.hex())
            except UnicodeDecodeError:
                 # If UTF-8 fails, definitely treat as hex bytes
                return '%d(%s): h"%s"' % (self.idx, self.type.name, self.val.hex())

        elif self.type == ProtoFieldType.GROUPSTART:
             # Value should be a ProtoBuf object, dump its fields recursively
             group_str = "\n".join(f"  {f}" for f in self.val.fields) # Indent group content
             return "%d(%s): {\n%s\n}" % (self.idx, self.type.name, group_str)
        elif self.type == ProtoFieldType.GROUPEND:
             # Should not typically be stored, but if they are:
             return "%d(%s):" % (self.idx, self.type.name)
        else:
            # Handle potential unknown types gracefully
            return "%d(UnknownType%d): %s" % (self.idx, self.type.value, repr(self.val))


class ProtoReader:
    def __init__(self, data):
        # Use io.BytesIO for easier handling of position and remaining data
        self.stream = io.BytesIO(data)
        self.len = len(data)

    @property
    def pos(self):
        return self.stream.tell()

    def seek(self, pos):
        self.stream.seek(pos)

    def isRemain(self, length):
        # Check if reading 'length' bytes is possible from current position
        return self.pos + length <= self.len

    def read0(self):
        if not self.isRemain(1):
            raise IndexError("Not enough data to read 1 byte")
        byte = self.stream.read(1)
        return byte[0] # read(1) returns bytes

    def read(self, length):
        if not self.isRemain(length):
            raise IndexError(f"Not enough data to read {length} bytes")
        return self.stream.read(length)

    def readInt32(self):
        return int.from_bytes(self.read(4), byteorder="little", signed=False)

    def readInt64(self):
        return int.from_bytes(self.read(8), byteorder="little", signed=False)

    def readVarint(self):
        vint = 0
        n = 0
        while True:
            byte = self.read0()
            vint |= (byte & 0x7F) << (7 * n)
            if byte < 0x80:
                break
            n += 1
            if n > 10: # Protect against malformed varints (max 64-bit)
                 raise ProtoError("Varint too long")
        return vint

    def readString(self):
        length = self.readVarint()
        if length < 0: # Should not happen with unsigned varint parsing
            raise ProtoError(f"Invalid string length: {length}")
        return self.read(length)

    def skipField(self, wire_type: int):
        """Skips a field based on its wire type."""
        if wire_type == ProtoFieldType.VARINT:
            self.readVarint() # Read and discard
        elif wire_type == ProtoFieldType.INT64:
            self.read(8) # Skip 8 bytes
        elif wire_type == ProtoFieldType.STRING:
            length = self.readVarint()
            self.read(length) # Skip length bytes
        elif wire_type == ProtoFieldType.GROUPSTART:
            # Recursively skip until matching GROUPEND
            depth = 1
            while depth > 0:
                if not self.isRemain(1): # Check before reading key
                    raise ProtoError("Data truncated while skipping group")
                start_pos = self.pos
                try:
                    key = self.readVarint()
                    wt = key & 0x7
                    if wt == ProtoFieldType.GROUPEND:
                        depth -= 1
                    elif wt == ProtoFieldType.GROUPSTART:
                        depth += 1
                    else:
                        self.skipField(wt) # Skip nested field's value
                except IndexError:
                    raise ProtoError(f"Data truncated while skipping group field near pos {start_pos}")
                except ProtoError as e:
                    raise ProtoError(f"Error skipping group field near pos {start_pos}: {e}") from e

        elif wire_type == ProtoFieldType.GROUPEND:
            # Should not be skipped standalone, indicates malformed data
            raise ProtoError("Attempting to skip standalone GROUPEND")
        elif wire_type == ProtoFieldType.INT32:
            self.read(4) # Skip 4 bytes
        else: # Includes any other invalid type
            raise ProtoError(f"Cannot skip unknown wire type {wire_type}")


class ProtoWriter:
    def __init__(self):
        self.data = bytearray()

    def write0(self, byte):
        self.data.append(byte & 0xFF)

    def write(self, bytes_data): # Renamed arg
        self.data.extend(bytes_data)

    def writeInt32(self, int32):
        bs = int32.to_bytes(4, byteorder="little", signed=False)
        self.write(bs)

    def writeInt64(self, int64):
        bs = int64.to_bytes(8, byteorder="little", signed=False)
        self.write(bs)

    def writeVarint(self, vint):
        # Handle 0 correctly
        if vint == 0:
            self.write0(0)
            return
        # Handle negative numbers if necessary (e.g., for sint32/sint64 zigzag)
        # For standard varint, assume non-negative or treat as unsigned
        temp_vint = vint
        while temp_vint > 0:
            byte = temp_vint & 0x7F
            temp_vint >>= 7
            if temp_vint > 0:
                byte |= 0x80
            self.write0(byte)

    def writeString(self, bytes_data):
        self.writeVarint(len(bytes_data))
        self.write(bytes_data)

    def toBytes(self):
        return bytes(self.data)


class ProtoBuf:
    def __init__(self, data=None):
        self.fields: list[ProtoField] = [] # Add type hint
        if data is not None:
            if isinstance(data, (bytes, bytearray)):
                if len(data) > 0:
                    reader = ProtoReader(data)
                    try:
                        self._parseFields(reader)
                    except IndexError as e:
                        # Provide context for truncation errors
                        raise ProtoError(f"Data truncated near position {reader.pos}: {e}") from e
                    except ProtoError as e: # Catch specific proto errors
                        # Add position info if possible (though might be deep in recursion)
                        raise ProtoError(f"{e} (parsing near position {reader.pos})") from e
                    except Exception as e: # Catch unexpected errors
                        raise ProtoError(f"Unexpected error parsing near position {reader.pos}: {e}") from e
            elif isinstance(data, dict):
                if len(data) > 0:
                    self._parseDict(data)
            else:
                raise ProtoError("Unsupported type(%s) to initialize ProtoBuf" % type(data).__name__)

    def _parseFields(self, reader: ProtoReader, group_idx_end: int | None = None):
        """
        Recursively parses fields from the reader.
        Stops if end of data is reached or if parsing a group and the
        matching GROUPEND tag is found.
        """
        while reader.isRemain(1): # Check if there's at least one byte for the key
            start_pos = reader.pos
            key = reader.readVarint()
            wire_type_val = key & 0x7
            field_idx = key >> 3

            if field_idx == 0:
                raise ProtoError(f"Invalid field index 0 encountered near position {start_pos}")

            try:
                field_type = ProtoFieldType(wire_type_val)
            except ValueError:
                # Handle unknown wire types by skipping them
                # print(f"Warning: Unknown wire type {wire_type_val} for field {field_idx} at pos {start_pos}. Skipping.")
                try:
                    reader.skipField(wire_type_val)
                    continue # Move to the next field
                except ProtoError as e:
                    raise ProtoError(f"Error skipping unknown field {field_idx} (type {wire_type_val}) near pos {start_pos}: {e}") from e
                except IndexError:
                     raise ProtoError(f"Data truncated while skipping unknown field {field_idx} (type {wire_type_val}) near pos {start_pos}")


            # Check if this is the end marker for the group we are currently parsing
            if group_idx_end is not None and field_type == ProtoFieldType.GROUPEND and field_idx == group_idx_end:
                return True # Signal that the group end was found

            # --- Process known field types ---
            value = None
            if field_type == ProtoFieldType.VARINT:
                value = reader.readVarint()
            elif field_type == ProtoFieldType.INT64:
                value = reader.readInt64()
            elif field_type == ProtoFieldType.STRING:
                value = reader.readString()
            elif field_type == ProtoFieldType.INT32:
                value = reader.readInt32()
            elif field_type == ProtoFieldType.GROUPSTART:
                # Create a nested ProtoBuf to hold the group's content
                nested_pb = ProtoBuf()
                # Recursively parse the group's content
                found_end = nested_pb._parseFields(reader, group_idx_end=field_idx)
                if not found_end:
                    # If the recursive call finished without finding the end marker
                    raise ProtoError(f"Unterminated group {field_idx} started near position {start_pos}")
                value = nested_pb # Store the parsed group object
            elif field_type == ProtoFieldType.GROUPEND:
                # We encountered a GROUPEND that doesn't match the one we're looking for (if any)
                raise ProtoError(f"Unexpected GROUPEND tag for field {field_idx} encountered near position {start_pos}")
            else:
                 # Should have been caught by unknown wire type check, but defense-in-depth
                 raise ProtoError(f"Unhandled wire type {field_type.name} parsing field {field_idx} near position {start_pos}")

            if value is not None:
                 self.put(ProtoField(field_idx, field_type, value))

        # If we reach here while parsing a group, it means data ended before GROUPEND
        if group_idx_end is not None:
            return False # Signal group end was NOT found

        return True # Finished parsing (top level or group successfully)


    # __parseBuf is now just the entry point
    def __parseBuf(self, data):
        reader = ProtoReader(data)
        try:
            self._parseFields(reader)
        except IndexError as e:
            raise ProtoError(f"Data truncated near position {reader.pos}: {e}") from e
        except ProtoError as e:
            # Add context if possible
             raise ProtoError(f"{e} (parsing near position {reader.pos})") from e
        except Exception as e:
            raise ProtoError(f"Unexpected error parsing near position {reader.pos}: {e}") from e


    def toBuf(self):
        writer = ProtoWriter()
        for field in self.fields:
            key = (field.idx << 3) | (field.type.value & 7)
            writer.writeVarint(key)

            if field.type == ProtoFieldType.VARINT:
                writer.writeVarint(field.val)
            elif field.type == ProtoFieldType.INT64:
                writer.writeInt64(field.val)
            elif field.type == ProtoFieldType.STRING:
                 # Ensure value is bytes if it was a nested ProtoBuf or dict
                 val_bytes = field.val
                 if isinstance(field.val, ProtoBuf):
                     val_bytes = field.val.toBuf()
                 elif not isinstance(field.val, bytes):
                      # Attempt conversion if needed, though ideally putBytes was used
                      try:
                          val_bytes = bytes(field.val)
                      except TypeError:
                           raise ProtoError(f"Cannot serialize non-bytes value for field {field.idx} (type {type(field.val)})")
                 writer.writeString(val_bytes)

            elif field.type == ProtoFieldType.INT32:
                writer.writeInt32(field.val)
            elif field.type == ProtoFieldType.GROUPSTART:
                 # Write the fields *within* the group recursively
                 if not isinstance(field.val, ProtoBuf):
                      raise ProtoError(f"Cannot serialize group field {field.idx}: value is not ProtoBuf object")
                 group_bytes = field.val.toBuf() # Serialize the nested fields
                 writer.write(group_bytes)
                 # Write the matching GROUPEND tag
                 end_key = (field.idx << 3) | (ProtoFieldType.GROUPEND.value & 7)
                 writer.writeVarint(end_key)
            elif field.type == ProtoFieldType.GROUPEND:
                 # GROUPEND is written automatically when serializing GROUPSTART
                 # Standalone GROUPEND fields should not exist / be serialized.
                 # raise ProtoError("Serialization of standalone GROUPEND field not supported.")
                 pass # Silently ignore if somehow present
            else:
                raise ProtoError(
                    "Encode to protobuf error, unexpected field type: %s for field %d"
                    % (field.type.name, field.idx)
                )
        return writer.toBytes()

    def dump(self, indent=""):
        for field in self.fields:
            if field.type == ProtoFieldType.GROUPSTART and isinstance(field.val, ProtoBuf):
                 print(f"{indent}{field.idx}({field.type.name}): {{")
                 field.val.dump(indent + "  ")
                 print(f"{indent}}}")
            else:
                 # Use the field's __str__ representation
                 print(f"{indent}{field}")

    def getList(self, idx):
        return [field for field in self.fields if field.idx == idx]

    # get() still returns the first match
    def get(self, idx):
        for field in self.fields:
            if field.idx == idx:
                return field
        return None

    # Convenience methods (getInt, getBytes etc.) still return based on the first match
    def getInt(self, idx):
        pf = self.get(idx)
        if pf is None:
            return 0 # Return default value
        if pf.type in (ProtoFieldType.INT32, ProtoFieldType.INT64, ProtoFieldType.VARINT):
            return pf.val
        raise ProtoError(f"Field {idx} is not an integer type (found {pf.type.name})")

    def getBytes(self, idx):
        pf = self.get(idx)
        if pf is None:
            return None
        if pf.type == ProtoFieldType.STRING:
             if not isinstance(pf.val, bytes):
                 # This might happen if parsed from dict with non-bytes string
                 # Try encoding, but this is ambiguous
                 try:
                      return str(pf.val).encode('utf-8') # Or another default?
                 except Exception:
                     raise ProtoError(f"Field {idx} (type {pf.type.name}) value is not bytes and couldn't be encoded easily: {type(pf.val)}")
             return pf.val
        # If it's a group, return its serialized bytes
        if pf.type == ProtoFieldType.GROUPSTART and isinstance(pf.val, ProtoBuf):
             return pf.val.toBuf()

        raise ProtoError(f"Field {idx} is not a bytes/string/group type (found {pf.type.name})")

    def getUtf8(self, idx):
        bs = self.getBytes(idx)
        if bs is None:
            return None
        try:
            return bs.decode("utf-8")
        except UnicodeDecodeError as e:
            raise ProtoError(f"Field {idx} contains non-UTF8 bytes: {e}") from e

    def getProtoBuf(self, idx):
        pf = self.get(idx)
        if pf is None:
            return None
        if pf.type == ProtoFieldType.STRING:
            try:
                # Return a new ProtoBuf parsed from the bytes
                return ProtoBuf(pf.val)
            except ProtoError as e:
                 raise ProtoError(f"Failed to parse bytes of field {idx} as ProtoBuf: {e}") from e
        # If it was parsed as a group, the value is already a ProtoBuf object
        if pf.type == ProtoFieldType.GROUPSTART and isinstance(pf.val, ProtoBuf):
            return pf.val

        raise ProtoError(f"Field {idx} is not a string/bytes or group type suitable for ProtoBuf (found {pf.type.name})")


    def put(self, field: ProtoField):
        self.fields.append(field)

    # Convenience put methods remain the same
    def putInt32(self, idx, int32):
        self.put(ProtoField(idx, ProtoFieldType.INT32, int32))

    def putInt64(self, idx, int64):
        self.put(ProtoField(idx, ProtoFieldType.INT64, int64))

    def putVarint(self, idx, vint):
        self.put(ProtoField(idx, ProtoFieldType.VARINT, vint))

    def putBytes(self, idx, data):
        if not isinstance(data, (bytes, bytearray)):
             raise TypeError(f"putBytes requires bytes or bytearray, got {type(data).__name__}")
        self.put(ProtoField(idx, ProtoFieldType.STRING, bytes(data))) # Ensure it's bytes

    def putUtf8(self, idx, data: str):
        if not isinstance(data, str):
             raise TypeError(f"putUtf8 requires str, got {type(data).__name__}")
        self.put(ProtoField(idx, ProtoFieldType.STRING, data.encode("utf-8")))

    def putProtoBuf(self, idx, data):
         # Allow putting either a ProtoBuf object or its serialized bytes
         if isinstance(data, ProtoBuf):
             self.put(ProtoField(idx, ProtoFieldType.STRING, data.toBuf()))
         elif isinstance(data, (bytes, bytearray)):
              self.put(ProtoField(idx, ProtoFieldType.STRING, bytes(data)))
         else:
             raise TypeError(f"putProtoBuf requires ProtoBuf, bytes, or bytearray, got {type(data).__name__}")

    # Add method for putting a group (represented by a ProtoBuf object)
    def putGroup(self, idx, group_pb: 'ProtoBuf'):
         if not isinstance(group_pb, ProtoBuf):
              raise TypeError(f"putGroup requires a ProtoBuf object, got {type(group_pb).__name__}")
         self.put(ProtoField(idx, ProtoFieldType.GROUPSTART, group_pb))

    def _parseDict(self, data: dict):
        """
        Convert dict object to ProtoBuf object.
        Keys must be integers (field numbers).
        Handles lists for repeated fields.
        """
        for k, v in data.items():
             if not isinstance(k, int) or k <= 0:
                 raise ProtoError(f"Invalid dictionary key for ProtoBuf field: {k}. Must be positive integer.")

             # Handle lists (repeated fields)
             values_to_process = v if isinstance(v, list) else [v]

             for item in values_to_process:
                 if isinstance(item, int):
                     # Determine best integer type? Default to VARINT.
                     # Could add hints or check range, but VARINT is flexible.
                     self.putVarint(k, item)
                 elif isinstance(item, str):
                     self.putUtf8(k, item)
                 elif isinstance(item, (bytes, bytearray)):
                     self.putBytes(k, bytes(item)) # Ensure bytes
                 elif isinstance(item, dict):
                      # Assume nested message, recursively convert
                     self.putProtoBuf(k, ProtoBuf(item))
                 elif isinstance(item, ProtoBuf):
                      # Allow putting ProtoBuf objects directly (could be group or message)
                      # Decide whether to store as GROUP or STRING based on its likely origin?
                      # Safest is to serialize it and store as STRING.
                      self.putProtoBuf(k, item)
                 else:
                     raise ProtoError(f"Unsupported value type ({type(item).__name__}) for field {k} in dict conversion")


    def toDict(self, out_template: dict) -> dict:
        """
        Convert ProtoBuf object to dict object based on a template dict.
        Fills values in the template based on field types. Handles first match only.
        Warning: This doesn't handle repeated fields well and relies on template structure.
        Consider using toDictAuto for a more general conversion.
        """
        out = out_template.copy() # Work on a copy
        for k, template_v in out.items():
             if not isinstance(k, int) or k <= 0:
                  print(f"Warning: Skipping invalid key in template dict: {k}")
                  continue

             field = self.get(k) # Gets first matching field
             if field is None:
                 # Keep template value or set to None/default? Let's keep template.
                 continue

             try:
                 if isinstance(template_v, int):
                     out[k] = self.getInt(k)
                 elif isinstance(template_v, str):
                     out[k] = self.getUtf8(k)
                 elif isinstance(template_v, bytes):
                     out[k] = self.getBytes(k)
                 elif isinstance(template_v, dict):
                      nested_pb = self.getProtoBuf(k)
                      if nested_pb:
                           out[k] = nested_pb.toDict(template_v) # Recursive call
                      else:
                           # Field exists but couldn't be parsed as ProtoBuf? Keep template.
                           pass
                 else:
                     # Keep template value if type doesn't match known conversions
                     pass
             except ProtoError as e:
                  print(f"Warning: Could not convert field {k} for template: {e}")
                  # Keep template value on error
                  pass
        return out

    def toDictAuto(self) -> dict:
        """
        Automatic conversion of ProtoBuf to dict.
        Handles repeated fields by creating lists.
        Recursively converts nested messages and groups.
        """
        intermediate = {} # Stores lists of values for each field index

        for field in self.fields:
            key = field.idx
            value = None

            if field.type in (ProtoFieldType.VARINT, ProtoFieldType.INT64, ProtoFieldType.INT32):
                value = field.val
            elif field.type == ProtoFieldType.STRING:
                 # Try decoding as UTF-8 first
                 try:
                     decoded_str = field.val.decode('utf-8')
                     # Heuristic: if it decodes and looks printable, treat as string
                     # This isn't perfect but often works.
                     if all(c.isprintable() or c.isspace() for c in decoded_str):
                          value = decoded_str
                     else:
                          # Decoded but non-printable suggests nested message or raw bytes
                          try:
                              nested_pb = ProtoBuf(field.val)
                              # Check if parsing yielded anything
                              if nested_pb.fields:
                                  value = nested_pb.toDictAuto()
                              else: # Empty parse result, treat as bytes
                                  value = field.val
                          except ProtoError: # Parsing failed, treat as bytes
                              value = field.val
                 except UnicodeDecodeError:
                      # Failed UTF-8 decode, try parsing as nested message
                      try:
                          nested_pb = ProtoBuf(field.val)
                          if nested_pb.fields:
                              value = nested_pb.toDictAuto()
                          else: # Empty parse result, treat as bytes
                              value = field.val
                      except ProtoError: # Parsing failed, treat as bytes
                           value = field.val

            elif field.type == ProtoFieldType.GROUPSTART:
                 # Value should be a ProtoBuf object from parsing
                 if isinstance(field.val, ProtoBuf):
                     value = field.val.toDictAuto() # Recursive call
                 else:
                      # Should not happen if parsing was correct
                      value = {"__error__": f"Group {key} value was not ProtoBuf object ({type(field.val).__name__})"}

            # Append value to the list for this key
            if value is not None:
                 if key not in intermediate:
                     intermediate[key] = []
                 intermediate[key].append(value)

        # Final result: single items are scalars, multiple items are lists
        result = {}
        for key, values in intermediate.items():
            if len(values) == 1:
                result[key] = values[0]
            else:
                result[key] = values

        return result


def parse(path_or_hex):
    """
    Parse proto file or hex string of proto bytes, then print using dump().
    """
    data = None
    source_desc = ""
    try:
        if os.path.isfile(path_or_hex):
            source_desc = f"file: {path_or_hex}"
            with open(path_or_hex, "rb") as file:
                data = file.read()
        elif os.path.exists(path_or_hex):
             print(f"Error: Path exists but is not a file: {path_or_hex}")
             return
        else:
            # Try interpreting as hex string
            source_desc = "hex string"
            data = bytes.fromhex(path_or_hex)

    except FileNotFoundError:
         print(f"Error: File not found: {path_or_hex}")
         return
    except ValueError:
         print(f"Error: Input is not a valid file path or hex string: {path_or_hex}")
         return
    except Exception as e:
         print(f"Error reading input '{path_or_hex}': {e}")
         return

    if data is not None:
        print(f"--- Parsing {source_desc} ---")
        if len(data) < 500: # Print hex only for reasonably small inputs
             print(f"Hex: {data.hex()}")
        else:
             print(f"Size: {len(data)} bytes")
        print("--- Decoded Fields ---")
        try:
            pb = ProtoBuf(data)
            pb.dump() # Use the enhanced dump method

            # Optionally, print the dict representation too
            # print("\n--- Automatic Dictionary Representation ---")
            # auto_dict = pb.toDictAuto()
            # import json
            # try:
            #     # Use json for pretty printing, handle bytes specially
            #     print(json.dumps(auto_dict, indent=2, default=lambda x: x.hex() if isinstance(x, bytes) else repr(x)))
            # except Exception as json_e:
            #      print(f"(Error formatting as JSON: {json_e})")
            #      print(auto_dict) # Fallback to standard print

        except ProtoError as e:
            print(f"\n!!! PARSE ERROR: {e} !!!")
        except Exception as e:
             import traceback
             print(f"\n!!! UNEXPECTED ERROR DURING PARSING OR DUMPING: {e} !!!")
             traceback.print_exc()
             
from parsing import parse_simplified_ocr_v2, sample_hex
from PIL import ImageDraw
import numpy as np
from PIL import Image
import io, requests
import struct

def extract_words_and_arrays(data):
    results = []
    
    def recursive_extract(data):
        if isinstance(data, dict):
            for key, value in data.items():
                if key == 2 and isinstance(value, str):
                    if isinstance(data.get(4), dict) and isinstance(data[4].get(1), dict):
                        results.append((value, list(data[4][1].values())))
                else:
                    recursive_extract(value)
        elif isinstance(data, list):
            for item in data:
                recursive_extract(item)
    
    recursive_extract(data)
    return results


def int_array_to_float_array(int_array):
    """
    Converts an array of signed 32-bit integers (representing float bytes)
    into an array of actual float values.
    """
    float_array = []
    if not isinstance(int_array, list):
        return None # Expecting a list
    for index, num in enumerate(int_array):
        try:
            bytes_data = num.to_bytes(4, byteorder='little', signed=True)
            float_value = struct.unpack('<f', bytes_data)[0] # Use '<' for little-endian explicitly
            # if index >= 4:
            #     float_value = int(float_value) # Convert to int if it's the last value
            float_array.append(float_value)
            
        except (OverflowError, struct.error, TypeError, ValueError):
            # print(f"Warning: Could not convert int {num} to float. Skipping coordinate set.")
            try:
                bytes_data = num.to_bytes(4, byteorder='little', signed=False)
                float_value = struct.unpack('<f', bytes_data)[0] # Use '<' for little-endian explicitly
                float_array.append(float_value)
            except:
                float_array.append(0) # Append None if conversion fails
            # pass
            

            # return None # Invalidate the whole coordinate set if one fails
    return float_array


def parseres(dico):
    final = []
    only = extract_words_and_arrays(dico)
    for text, bbox in only:
        bbox = int_array_to_float_array(bbox)
        if bbox is not None:
            final.append({"text": text, "coordinates": bbox})
    return final


bs = bytes.fromhex(sample_hex) # A section of bytes data of protobuf
proto = ProtoBuf(bs)  # Convert bytes to protobuf objects
dico = proto.toDictAuto()

url = "https://lensfrontend-pa.googleapis.com/v1/crupload"

headers = {
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
    "Content-Length": "35218",
    "Content-Type": "application/x-protobuf",
    "Host": "lensfrontend-pa.googleapis.com",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "no-cors",
    "Sec-Fetch-Site": "none",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "X-Client-Data": "COL0ygE=",
    "X-Goog-Api-Key": "AIzaSyA2KlwBX3mkFo30om9LUFYQhpqLoa_BNhE"
}

def lensdetect(img):
    copyimg = img.copy()
    # copyimg.thumbnail((1000,1000), Image.Resampling.LANCZOS)
    # put black border
    # copyimg = Image.new("RGB", (copyimg.size[0] + 20, copyimg.size[1] + 20), (0, 0, 0))
    # copyimg.paste(img, (10, 10))

    imgByteArr = io.BytesIO()
    copyimg.save(imgByteArr, format='JPEG')
    imgByteArr = imgByteArr.getvalue()
    imgwidth, imgheight = copyimg.size
    dico[1][3][1][1] = imgByteArr
    dico[1][3][3][1] = imgheight
    dico[1][3][3][2] = imgwidth
    edited_proto = ProtoBuf(dico)
    edited_bytes = edited_proto.toBuf()
    response = requests.post(url, headers=headers, data=edited_bytes)
    response_bytes = response.content
    protores = ProtoBuf(response_bytes)
    dicores = protores.toDictAuto()
    # res = dicores[2][3][1][1]
    # target_lang = dicores[2][3][2]
    resultat = parseres(dicores)
    for part in resultat:
        text = part["text"]
        coords = part["coordinates"]
        angle = coords[4] * 180 / 3.141592653589793
        coords[4] = angle
        coords[5] = angle
    print(resultat)
    return resultat

    # resultat = parse_simplified_ocr_v2(res)
    # letters = []
    # for part in resultat:
    #     lettersBox = part["lettersBox"]
    #     for letter in lettersBox:
    #         print(letter["text"])
    #         letter["coordinates"][4] = letter["coordinates"][4] * 180 / 3.141592653589793
    #         letters.append(letter)
    # return letters

def get_rotated_rectangle_points(bbox, angle_degrees):
    x, y, width, height = bbox

    angle_rad = np.radians(angle_degrees)

    center_x = x + width / 2
    center_y = y + height / 2
    
    corners = [
        (-width/2, -height/2),
        (width/2, -height/2),
        (width/2, height/2),
        (-width/2, height/2)
    ]
    
    rotation_matrix = np.array([
        [np.cos(angle_rad), -np.sin(angle_rad)],
        [np.sin(angle_rad), np.cos(angle_rad)]
    ])
    
    rotated_corners = []
    for corner in corners:
        rotated_corner = np.dot(rotation_matrix, corner)
        rotated_corner[0] += center_x
        rotated_corner[1] += center_y
        rotated_corners.append(tuple(rotated_corner))
    
    return rotated_corners


if __name__ == "__main__":
    img = Image.open("test.jpg")
    letters = lensdetect(img)
    imgwidth, imgheight = img.size
    draw = ImageDraw.Draw(img)

    for letter in letters:
        coords = letter["coordinates"]
        centerPerX, centerPerY, perWidth, perHeight, angle, _ = coords
        width = perWidth * imgwidth
        height = perHeight * imgheight
        x = (centerPerX * imgwidth) - (width / 2)
        y = (centerPerY * imgheight) - (height / 2)
        x2 = x + width
        y2 = y + height
        bbox = (x, y, width, height)
        draw.rectangle([x, y, x2, y2], outline="blue", width=2)
        rotated_corners = get_rotated_rectangle_points(bbox, angle)
        draw.polygon(rotated_corners, outline="red", width=2)

    img.show()

# for letter in as_before(resultat):
#     coords = letter["coordinates"]
#     centerPerX, centerPerY, perWidth, perHeight, angle, _ = coords
#     width = perWidth * imgwidth
#     height = perHeight * imgheight
#     x = (centerPerX * imgwidth) - (width / 2)
#     y = (centerPerY * imgheight) - (height / 2)
#     x2 = x + width
#     y2 = y + height

#     # draw rectangle rotated
#     bbox = (x, y, width, height)
#     draw.rectangle([x, y, x2, y2], outline="blue", width=2)
#     rotated_corners = get_rotated_rectangle_points(bbox, angle)
#     draw.polygon(rotated_corners, outline="red", width=2)


# img.show()

