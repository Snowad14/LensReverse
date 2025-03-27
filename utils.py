# import struct


# # def extract_words_and_arrays(data):
# #     result = {}
    
# #     def recursive_extract(data):
# #         if isinstance(data, dict):
# #             for key, value in data.items():
# #                 if key == 2 and isinstance(value, str):
# #                     if isinstance(data.get(4), dict) and isinstance(data[4].get(1), dict):
# #                         result[value] = list(data[4][1].values())
# #                 else:
# #                     recursive_extract(value)
# #         elif isinstance(data, list):
# #             for item in data:
# #                 recursive_extract(item)
    
# #     recursive_extract(data)
# #     return result

# # print(extract_words_and_arrays(ret))

# def int_array_to_float_array(int_array):
#     float_array = []
#     for num in int_array:
#         bytes_data = num.to_bytes(4, byteorder='little', signed=True)
#         float_value = struct.unpack('f', bytes_data)[0]
#         float_array.append(float_value)
#     return float_array

# int_array = [1057378846, 1049723690, 1010995036, 1019348324, -1173569972, 1]
# float_array = int_array_to_float_array(int_array)
# print(float_array)
