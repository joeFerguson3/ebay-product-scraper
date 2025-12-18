# Sorts products by the number of sales
data = {}
with open("data.txt", "r") as f:
    lines = f.read().split("\n")
    print(lines[1])
    for l in lines:
       s = l.split()
       print(s)
       if(len(s) > 1):
        data[s[1]] = int(s[0])

sorted_data = dict(sorted(data.items(), key=lambda item: item[1]))

with open("sort_data/sorted_data.txt", "w") as f:
    f.write("")
    
for key, value in sorted_data.items():
    with open("sort_data/sorted_data.txt", "a") as f:
        f.write(key + " " + str(value) + "\n")


print(sorted_data)