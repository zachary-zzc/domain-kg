# 语义复核：完整歧义与参考问题清单

共 210 道；全是已完成队列中真实出现的问题。149 道原标可答、61 道原标无答案。每道均对所有模型/方案采用相同主表排除规则；不是所有条目都是错误金标。仍不清楚的参考、范围及全库无答案状态不会强行补一个答案。

下列理由保留逐题复核时的英文记录，原题与金标不改写。证据摘录只是定位辅助；完整段落/句子及全部答案组保存在私有复核 JSON 中。`原支持` 是旧金标使用的证据；`原上下文` 表示原无答案题没有正向支持证据。

[汇总与模型比较](full-semantic-review.md)

## 006 · drop · drop:history_1415:6629ae4d-99a4-4786-a8e8-4c0d723dc231:b2a3442fdcac

**原题：** What year did the Great Turkish War end?

**原金标：** `[["1698"], ["1698"], ["1698"]]`；原标可答。

**问题类型：** `source_world_conflict`。

**裁定：** Historical 1699 endpoint conflicts with arithmetic gold 1683+15=1698; previously adjudicated and documented.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-5b32d7dfbe12291c2c019a17` · history_1415：The Siege of Vienna in 1529 was the first attempt by the Ottoman Empire, led by Suleiman the Magnificent, to capture the city of Vienna, Austria. The siege signalled the pinnacle of the Ottoman Empire's power and the maximum extent of Ottoman expansion in cent…

## 009 · drop · drop:history_1720:d12821ea-a2ad-48cf-ba7e-7d456a7b045e:01576b735a62

**原题：** How many of the personnel were not civil servants?

**原金标：** `[["14169"], ["14506"], ["14506"]]`；原标可答。

**问题类型：** `scope_dependent`, `reference_ambiguity`。

**裁定：** Question omits armed forces/date; source has two nested personnel totals, yielding competing civil-servant complements.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-e3a19bd571dc6f2ba9e9b241` · history_1720：The total number of active military personnel in the Croatian Armed Forces stands at 14,506 and 6,000 reserves working in various service branches of the armed forces. In May 2016, Armed Forces had 16,019 members, of which 14,506 were active military personnel…

## 010 · drop · drop:history_1782:aec757e7-1807-4b63-b8de-2ec49edbed44:95c4b75dcb14

**原题：** How many passes did Aikman not complete in the first half?

**原金标：** `[["4"], ["4"], ["4"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** Game/date omitted; first-half Aikman passing statistics require original article scope.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-312ac18e7627ec524c718c9d` · history_1782：One week later, Dallas faced the 1993 San Francisco 49ers season in the NFC Championship Game for the second year in a row in what was, at the time, the last NFL game to air on CBS. The 49ers had the NFLs highest scoring offense with 473 points, 97 more than t…

## 011 · drop · drop:history_1793:402f5c6a-d0fe-475e-92f9-cc07d65b44ed:c85db6e3eef2

**原题：** How many percentage points out of 100 (what percent) of the median household income in the UK is the lowest that is considered to be above the poverty line?

**原金标：** `[["61"], ["60"], ["60"], ["61"], ["61"]]`；原标可答。

**问题类型：** `threshold_ambiguity`。

**裁定：** Lowest above a 60% boundary can mean 60% threshold or 61% for integer percentages; gold includes both.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-f6def3bfeff8051970959ed0` · history_1793：The poverty line in the UK is commonly defined as being 60% of the median household income. In 2007-2008, this was calculated to be £115 per week for single adults with no dependent children; £199 per week for couples with no dependent children; £195 per week …

## 012 · drop · drop:history_1793:668c769d-100b-445b-903d-d433912f2855:934508c53c7d

**原题：** How many percent of children in the country lived above the poverty line?

**原金标：** `[["69"], ["69"], ["69"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** Country/year omitted from child poverty percentage question.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-f6def3bfeff8051970959ed0` · history_1793：The poverty line in the UK is commonly defined as being 60% of the median household income. In 2007-2008, this was calculated to be £115 per week for single adults with no dependent children; £199 per week for couples with no dependent children; £195 per week …

## 013 · drop · drop:history_1859:0b79dd95-d368-4e15-802e-894becceb714:39f0754301d4

**原题：** How many percent are not non-families?

**原金标：** `[["66.6"], ["66.6"], ["33.4"]]`；原标可答。

**问题类型：** `scope_dependent`, `gold_error`。

**裁定：** Original source non-families=33.4%, so complement=66.6%; gold also incorrectly accepts 33.4. Target locality omitted.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-20bae0000a34f971c07492f5` · history_1859：There were 88,296 households out of which 29.5% had children under the age of 18 living with them, 53.3% were Marriage living together, 9.1% had a female householder with no husband present, and 33.4% were non-families. 26.1% of all households were made up of …

## 014 · drop · drop:history_1859:e573d564-b939-438f-b913-4f2f7b515e3e:6fa9e3fe2775

**原题：** Which groups in average are under 4 in size?

**原金标：** `[["household size", "family size"], ["household size", "family size"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** Household/family group-size comparison requires the omitted census locality/source.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-20bae0000a34f971c07492f5` · history_1859：There were 88,296 households out of which 29.5% had children under the age of 18 living with them, 53.3% were Marriage living together, 9.1% had a female householder with no husband present, and 33.4% were non-families. 26.1% of all households were made up of …

## 015 · drop · drop:history_1884:5c2d094c-357f-4638-ac28-d298e8dbbf15:090e2c7f38d7

**原题：** How many percent were not  Irish?

**原金标：** `[["92.2"], ["47"], ["92.2"]]`；原标可答。

**问题类型：** `scope_dependent`, `gold_error`。

**裁定：** No county/date identified; multiple census percentages are equally plausible; original gold also has spurious 47.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-0fffdabac3a8819a35c105ca` · history_1884：As of the census of 2000, there were 250,291 people, 99,187 households, and 60,702 families residing in the county. The population density was 298 people per square mile (115/km²). There were 104,217 housing units at an average density of 124 per square mile (…

## 017 · drop · drop:history_1892:e7fcb239-97da-4484-a0e9-56be58bf897b:cf23a3a71abe

**原题：** How many percent do not have children under the age of 18 living with them?

**原金标：** `[["70.5"], ["70.5"], ["70.5"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** No locality/date identified for household percentage; retrieved sources give several valid complements.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-908df7f6122e82996101db98` · history_1892：There were 74,285 households, out of which 21,922 (29.5%) had children under the age of 18 living in them, 36,729 (49.4%) were marriage living together, 7,685 (10.3%) had a female householder with no husband present, 3,804 (5.1%) had a male householder with no…

## 018 · drop · drop:history_1898:86a9397a-9d4c-4733-9603-dde272a86496:cf8e661ad149

**原题：** which household was fourth most common?

**原金标：** `[["under the age of 18"], ["under the age of 18"], ["under the age of 18"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** No population or set of household categories is specified for the fourth-place ranking.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-c7b4b340948466d0525a2d23` · history_1898：There were 1,882 households of which 21.4% had children under the age of 18 living with them, 36.4% were Marriage living together, 9.7% had a female householder with no husband present, 3.6% had a male householder with no wife present, and 50.4% were non-famil…

## 019 · drop · drop:history_1940:db6cc06e-0c2a-42f0-be9b-3ef19473fb26:14f1beb62a15

**原题：** Which groups are at least 30000 in size?

**原金标：** `[["people", "housing units"], ["people", "housing units"], ["people", "housing units"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** Groups at least 30000 is unbounded across the entire corpus; no locality or source scope identifies the intended people/housing categories.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-5a98c25846b89f42d6efcd2a` · history_1940：As of the census of 2000, there were 71,535 people, 29,466 households, and 18,416 families residing in the county. The population density was 102 people per square mile (39/km²). There were 31,939 housing units at an average density of 46 per square mile (18/k…

## 020 · drop · drop:history_1959:8536a970-3850-4ad4-9626-4eab30af76e4:39a21675bc8d

**原题：** How many more percent of people were white than Native American?

**原金标：** `[["69.7"], ["69.7"], ["69.7"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** Question gives no locality/year; several census sources have different valid white-minus-Native-American values.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-506c5fb80ca55bf76c70a862` · history_1959：The 2010 United States Census reported that Lassen County had a population of 34,895. The racial makeup of Lassen County was 25,532 (73.2%) White (U.S. Census), 2,834 (8.1%) African American (U.S. Census), 1,234 (3.5%) Native American (U.S. Census), 356 (1.0%)…

## 021 · drop · drop:history_2011:d06c65cc-777e-461d-aa68-cadc9da7d890:aaa568918a2c

**原题：** What year was the GDP higher, 2007 or 2008?

**原金标：** `[["2007"], ["2007"], ["2007"]]`；原标可答。

**问题类型：** `measure_ambiguity`, `gold_error`。

**裁定：** Source compares GDP growth rates 7% and 1%, not GDP levels. Question says GDP; gold 2007 follows growth, but positive 2008 growth implies a higher real level.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-1b6592cf2605baf4084904bd` · history_2011：After Mwai Kibaki took over as president in 2003,the GDP growth quickly picked up to 2.3% in 2004 reaching an all time high of 7% in 2007.GDP growth slumped to 1% in 2008 due to post election violence,then picked up to 2.9% in 2009 before averaging 5% between …

## 022 · drop · drop:history_2064:1e221f63-9654-4ba4-98ed-355cca7c401b:61e4e44357a6

**原题：** Which three teams hold the record for 18 total wins in a season?

**原金标：** `[["49ers", "Bears", "New England Patriots"], ["Bears", "Patriots", "49ers"], ["3"]]`；原标可答。

**问题类型：** `gold_error`。

**裁定：** The three NFL teams are source-supported, but extra gold alternative 3 does not name the requested teams.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-1e1b3ec6275ce3a54406f653` · history_2064：In 1984, the 49ers had one of the greatest seasons in team history by finishing the regular season 15-1-0, setting the record for most regular season wins that was later equaled by the 1985 Chicago Bears, the 1998 Minnesota Vikings, the 2004 Pittsburgh Steeler…

## 024 · drop · drop:history_2142:8b3b94d8-dbdf-41a7-959e-6349e62db7ae:ebaddb97f2f5

**原题：** Which nationalities each made up over 8% of the population?

**原金标：** `[["German American", "English American", "Irish American"], ["4"], ["German American", "English American", "Irish American"]]`；原标可答。

**问题类型：** `scope_dependent`, `gold_error`。

**裁定：** Target county omitted, multiple census populations exist, and gold includes non-answer alternative 4.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-157d28b62185bf28b946ed61` · history_2142：As of the census of 2000, there were 445,342 people, 169,162 households, and 114,015 families residing in the county. The population density was 615 people per square mile (238/km²). There were 178,913 housing units at an average density of 247 per square mile…

## 025 · drop · drop:history_2151:548ea9c0-ab86-46dd-a023-cb30ee1f9625:a41737156e5f

**原题：** Which age group had the second most people?

**原金标：** `[["under the age of 18"], ["under the age of 18"], ["under the age of 18"], ["under the age of 18"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** Population and source not named; competing age distributions have different second-ranked groups.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-6f32c7f7f756adcabc65a031` · history_2151：In the county, the population was spread out with 26.20% under the age of 18, 9.30% from 18 to 24, 26.50% from 25 to 44, 23.50% from 45 to 64, and 14.60% who were 65 years of age or older. The median age was 37 years. For every 100 females there were 95.90 mal…

## 026 · drop · drop:history_2162:29c05556-cec1-4e8c-8f33-e973cd51f7bf:da7544a00cc0

**原题：** How many residents, in terms of percentage, were younger than 25?

**原金标：** `[["66.3"], ["66.3"], ["66.3"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** Locality/year omitted; several retrieved under-25 percentages are each valid for different populations.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-c495b7a826b3355991f77b3b` · history_2162：The median age in the city was 22.1 years. 10.1% of residents were under the age of 18; 56.2% were between the ages of 18 and 24; 16.1% were from 25 to 44; 10.5% were from 45 to 64; and 7% were 65 years of age or older. The gender makeup of the city was 64.3% …

## 027 · drop · drop:history_2164:6a8ad68c-a109-41c2-a803-0f574786355e:1f673a0d9060

**原题：** How many more males were there than females out of every 100 females?

**原金标：** `[["60.7"], ["60.7"], ["60.7"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** No locality/year specified for sex ratio; plausible answers range from negative to positive depending on population.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-5e78abd127cea1c48fadca2c` · history_2164：In the city, the population was spread out with 12.0% under the age of 18, 55.2% from 18 to 24, 15.3% from 25 to 44, 10.3% from 45 to 64, and 7.1% who were 65 years of age or older. The median age was 22 years. For every 100 females, there were 160.7 males. Fo…

## 028 · drop · drop:history_2187:b800e775-04a8-45c3-bf56-a3f0af116544:1a3df865d92e

**原题：** How many more people were there than households?

**原金标：** `[["154608"], ["154608"], ["154608"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** No locality/year specified for population minus households; no unique target.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-4196507fd71caf5bb9befd24` · history_2187：As of the 2010 United States Census, there were 244,826 people, 90,218 households, and 66,335 families residing in the county. The population density was . There were 95,554 housing units at an average density of . The racial makeup of the county was 81.2% whi…

## 030 · drop · drop:history_2671:14e21b4c-fead-4a63-8baa-d8e84002fb60:78ce1023eb22

**原题：** Which groups are at least 5800 in size?

**原金标：** `[["people", "housing units"], ["housing units"], ["people", "housing units"]]`；原标可答。

**问题类型：** `scope_dependent`, `gold_error`。

**裁定：** Groups at least 5800 is unbounded across corpus; locality omitted and reference alternative housing units omits another expected category.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-e9e65fc251038d2608f2cf56` · history_2671：As of the census of 2000, there were 15,633 people, 5,734 households, and 4,432 families residing in the town. The population density was . There were 5,879 housing units at an average density of . The racial makeup of the town was 95.42% Race and ethnicity in…

## 032 · drop · drop:history_2762:9a2ae3fc-1ae8-4645-babd-efff4f03a189:c28dc7af195e

**原题：** How many more, in percentage, of migrants were from other parts of the state as compared to from rest of India?

**原金标：** `[["27.7"], ["23"], ["61.5"]]`；原标可答。

**问题类型：** `scope_dependent`, `gold_error`。

**裁定：** City omitted; source subtraction is 61.5-33.8=27.7, while extra gold alternatives 23 and 61.5 do not answer the difference.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-bf4108ecab27be6b208dac83` · history_2762：A resident of Chennai is called a Chennaite. According to 2011 census, the city had a population of 4,646,732, within the area administered by the Municipal Corporation; that had 11 lakh households, with 51% of them living in rented houses. The citys limits we…

## 034 · drop · drop:history_2777:60da4590-2d0f-483c-80e6-1692ba8d8193:b19f7a10a9b6

**原题：** How many more people are there than housing units?

**原金标：** `[["25422"], ["25422"], ["25422"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** No locality/year specified for population minus housing units; multiple valid scoped differences.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-d65a53f2cfdf80756287c6c7` · history_2777：As of the census of 2000, there were 49,927 people, 18,009 households, and 12,192 families residing in the county. The population density was 48 people per square mile (19/km²). There were 24,505 housing units at an average density of 24 per square mile (9/km²…

## 035 · drop · drop:history_2777:ba3fd4f2-3f72-466f-99a9-cd8d30a63f16:c7bc091ef6b8

**原题：** Which ancestral group is larger: Irish or Italian?

**原金标：** `[["Irish"], ["Irish"], ["Irish"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** No target population/locality is identified; competing demographic tables in the global corpus give different valid values/categories.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-d65a53f2cfdf80756287c6c7` · history_2777：As of the census of 2000, there were 49,927 people, 18,009 households, and 12,192 families residing in the county. The population density was 48 people per square mile (19/km²). There were 24,505 housing units at an average density of 24 per square mile (9/km²…

## 036 · drop · drop:history_2788:c02721e7-7bb2-489b-9741-9e3b7837a42a:6da80260eae7

**原题：** How many more people, in terms of percentage, were White compared to Asian and Native American combined?

**原金标：** `[["95.89"], ["95.89"], ["95.89"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** No target population/locality is identified; competing demographic tables in the global corpus give different valid values/categories.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-494ac047719c49b5a37b7829` · history_2788：As of the census of 2000, there were 952 people, 392 households, and 241 families residing in the village. The population density was 952.9 people per square mile (367.6/km²). There were 449 housing units at an average density of 449.4 per square mile (173.4/k…

## 037 · drop · drop:history_2815:066a1508-ed37-4497-8fbb-e19826e3591f:0a7f7db2bef8

**原题：** How many people, in terms of percentage of the population, were not solely White or solely African American??

**原金标：** `[["16.77"], ["16.77"], ["16.77"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** No target population/locality is identified; competing demographic tables in the global corpus give different valid values/categories.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-1801f9dc65b18a3132d89e54` · history_2815：As of the 2000 United States Census of 2000, there were 47,829 people, 15,137 households, and 10,898 families residing in the city. The population density was 7,921.7 people per square mile (3,057.4/km2). There were 16,180 housing units at an average density o…

## 038 · drop · drop:history_2848:99c10414-055c-46cb-8e98-a8a1e8cd51e6:cf32bedad680

**原题：** Which two ethnicities made up the largest percentages of the Hispanic and Latino Americans and Latinos of any race group?

**原金标：** `[["Puerto Rican", "Dominican"], ["33.2% Puerto Rican", "1.7% Dominican"], ["Puerto Rican", "Dominican"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** No target population/locality is identified; competing demographic tables in the global corpus give different valid values/categories.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-786ba7db82f5d6aa66fa00bb` · history_2848：In terms of race and ethnicity, Springfield is 51.8% White American, 22.3% African American or African American, 0.6% American Indian and Alaska Native, 2.4% Asian (1.2% Vietnamese, 0.3% Chinese, 0.2% Indian, 0.1% Cambodian, 0.1% Filipino, 0.1% Korean, 0.1% Pa…

## 039 · drop · drop:history_2857:2a550127-6805-4c6f-9e41-a36180e44618:811be61f354d

**原题：** How many more people, by percent, living in the county were age 45 to 64 compared to 25 to 44?

**原金标：** `[["2.6"], ["2.6"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** No target population/locality is identified; competing demographic tables in the global corpus give different valid values/categories.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-a7543838251e2a0061bf6c1c` · history_2857：In the county, the population is spread out with 24.60% under the age of 18, 6.40% from 18 to 24, 22.80% from 25 to 44, 25.40% from 45 to 64, and 20.80% who are 65 years of age or older. The median age is 42 years. For every 100 females there are 95.90 males. …

## 042 · drop · drop:history_2941:b083006d-c58e-4cdc-abf2-91a0fa6e2924:f1bae71e2e9c

**原题：** How many years after producing Molloy did Beckett produce Rockaby?.

**原金标：** `[["30"], ["10"], ["30"]]`；原标可答。

**问题类型：** `gold_error`。

**裁定：** Beckett Molloy 1951 and Rockaby 1981 imply 30 years; extra gold 10 is incompatible.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-b4dd53669f5512b0fbcd263e` · history_2941：While The Oxford Encyclopedia of British Literature states that modernism ended by c. 1939 with regard to British and American literature, "When (if) Modernism petered out and postmodernism began has been contested almost as hotly as when the transition from V…

## 043 · drop · drop:history_2951:07c500f3-208b-4a5c-bf34-3adf80cf03fe:1c8c34aee1c6

**原题：** How many more percent of households did not have a car in 2015 than in 2016?

**原金标：** `[["2"], ["2"], ["2"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** The 2015/2016 car-ownership question does not identify the relevant city; context needed to determine target.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-fe63a2a1436233d8bc62314f` · history_2951：According to the 2016 American Community Survey, 78.1% of working Nashville residents commuted by driving alone, 9.8% carpooled, 2% used public transportation, and 2.2% walked. About 1.1% used all other forms of transportation, including taxicab, motorcycle, a…

## 044 · drop · drop:history_2991:7c2592f6-2f7c-4e79-b8bd-d5819e3e3ed0:6f0de79b9796

**原题：** How many more percent of the population was female?

**原金标：** `[["2.6"], ["2.6"], ["2.6"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** No target population/locality is identified; competing demographic tables in the global corpus give different valid values/categories.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-7bd344b88a9315ac81bedc33` · history_2991：In 2016, there were 122,051 people residing in Syracuse, located in the province of Syracuse, Sicily, of whom 48.7% were male and 51.3% were female. Minors (children ages 18 and younger) totalled 18.87 percent of the population compared to pensioners who numbe…

## 045 · drop · drop:history_3048:61341758-6e1f-45bc-9596-dfe8d0a7902e:70b655463d1a

**原题：** How many more females per 100 were there than males?

**原金标：** `[["2.7"], ["5.5"], ["2.7"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** No target population/locality is identified; competing demographic tables in the global corpus give different valid values/categories.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-c82a408d26eba7a035f6cc4e` · history_3048：In the city, the population was distributed as 28.3% under the age of 18, 11.3% from 18 to 24, 32.7% from 25 to 44, 18.2% from 45 to 64, and 9.6% who were 65 years of age or older. The median age was 31 years. For every 100 females, there were 97.3 males. For …

## 046 · drop · drop:history_3048:8560cdcf-1727-47e8-b87e-054ab61ebae9:f348446161ed

**原题：** How many in percent weren't 45 to 64?

**原金标：** `[["81.8"], ["81.8"], ["81.8"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** No target population/locality is identified; competing demographic tables in the global corpus give different valid values/categories.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-c82a408d26eba7a035f6cc4e` · history_3048：In the city, the population was distributed as 28.3% under the age of 18, 11.3% from 18 to 24, 32.7% from 25 to 44, 18.2% from 45 to 64, and 9.6% who were 65 years of age or older. The median age was 31 years. For every 100 females, there were 97.3 males. For …

## 047 · drop · drop:history_3048:bddba6b8-d3c5-46a7-91c5-c1578b521d2b:deedac16f520

**原题：** How many in percent weren't 25 to 44?

**原金标：** `[["67.3"], ["67.3"], ["67.3"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** No target population/locality is identified; competing demographic tables in the global corpus give different valid values/categories.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-c82a408d26eba7a035f6cc4e` · history_3048：In the city, the population was distributed as 28.3% under the age of 18, 11.3% from 18 to 24, 32.7% from 25 to 44, 18.2% from 45 to 64, and 9.6% who were 65 years of age or older. The median age was 31 years. For every 100 females, there were 97.3 males. For …

## 048 · drop · drop:history_3058:8de3051f-fb9e-4646-b479-445368f80f88:0cfb89e44222

**原题：** How many percent of residents do not speak Hmong-Mien languages?

**原金标：** `[["98.8"], ["98.8"], ["98.8"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** No target population/locality is identified; competing demographic tables in the global corpus give different valid values/categories.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-ff58e9b9e7b01f2e91555010` · history_3058：Among Richmond residents, 64.56% residents speak English, 23.13% speak Spanish, 2.11% speak Tagalog language, 1.75% speak Chinese, 1.20% speak Hmong-Mien languages, 1.12% speak Laotian language, 0.72% speak Punjabi language, 0.54% speak Cantonese language, 0.5…

## 049 · drop · drop:history_3089:5d72920d-ed5f-4470-9e6a-b0313f31095b:23bde274edfc

**原题：** Which groups in percent are larger than 22%?

**原金标：** `[["under age 18", "45 to 64"], ["under age 18", "45 to 64"], ["under age 18", "45 to 64"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** No target population/locality is identified; competing demographic tables in the global corpus give different valid values/categories.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-df9cbd20962593d4d69ce163` · history_3089：The population consisted of 5,841 people (25.3%) under age 18, 1,875 people (8.1%) age 18 to 24, 5,025 people (21.8%) age 25 to 44, 7,414 people (32.2%) age 45 to 64, and 2,903 people (12.6%) age 65 or older. The median age was 41.6 years. For every 100 female…

## 050 · drop · drop:history_3089:6beb69d5-de04-4e0c-9987-80890ddfc2e8:2eb4daa51779

**原题：** How many in percent weren't under age 18?

**原金标：** `[["74.7"], ["74.7"], ["74.7"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** No locality/year named for the under-18 complement; several populations provide different values.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-df9cbd20962593d4d69ce163` · history_3089：The population consisted of 5,841 people (25.3%) under age 18, 1,875 people (8.1%) age 18 to 24, 5,025 people (21.8%) age 25 to 44, 7,414 people (32.2%) age 45 to 64, and 2,903 people (12.6%) age 65 or older. The median age was 41.6 years. For every 100 female…

## 052 · drop · drop:history_325:2561ebde-4827-4eea-b0aa-01bbd0f4d3b6:28677f3dd167

**原题：** How many titles were granted to Lévis?

**原金标：** `[["3"], ["3"], ["2"], ["3"], ["3"]]`；原标可答。

**问题类型：** `counting_ambiguity`。

**裁定：** Source lists governorship, marshal rank and ducal title; whether office/rank count as titles is unspecified, and gold accepts both 2 and 3.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-6a7cdeba1a28e2752415d904` · history_325：Lévis returned to France on parole, and was released for service in Europe by William Pitt. He served in the German campaigns of 1762, and retired from active military service when the war ended in 1763 with the Peace of Paris. He was appointed governor of Art…

## 055 · drop · drop:history_351:9c435191-072f-4543-a119-b77dc7f45d35:fe07b1970991

**原题：** How many years did dominion over the islands changed hands?

**原金标：** `[["27"], ["59"], ["27"], ["27"]]`；原标可答。

**问题类型：** `gold_error`, `scope_dependent`。

**裁定：** Phrase dominion changed hands identifies 1418-1445=27 years; alternate gold59 uses a different endpoint1477. The question additionally does not name which islands; gold 27 fits the original 1418–1445 passage, but pooled-corpus scope is missing.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-0e3be254875a35b3330d9592` · history_351：The Bethencourt era ended in 1418 when Maciot sold his holdings and the rights to subjugate the remaining islands to Enrique Pérez de Guzmán. From this point on the intervention of the King of Castile increased. Between 1418 and 1445 dominion over the islands …

## 056 · drop · drop:history_3581:9c3c4052-e775-4667-9537-07f7ab6dc016:e4ef8d51c39e

**原题：** How many people living in Bolivia with HIV/AIDS in 2009 did not die from it?

**原金标：** `[["11000"], ["11000"], ["11000"]]`；原标可答。

**问题类型：** `underdetermined_quantity`。

**裁定：** Source gives estimated12000 and LESS THAN1000 deaths; survivors exceed11000, not an exact11000 as the gold states.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-56380493af6f2f034bff0b9c` · history_3581：Healthcare expenditures comprise only 4.9% of the GDP. According to the 2013 World Factbook, Bolivia ranks 161st in life expectancy with an average age of 68.2 years. In 2009, the World Factbook estimated 12,000 people are living in Bolivia with HIV/AIDS (0.2%…

## 057 · drop · drop:history_3992:3a61104e-e20d-4436-ab16-b3e6a6da604c:fdb3853aee17

**原题：** How many years after Homeserve was a sponsor for the club did Palm Eco-Town Development sponsor them?

**原金标：** `[["7"], ["7"], ["7"]]`；原标可答。

**问题类型：** `temporal_reference_ambiguity`。

**裁定：** Sponsorships are ranges2010-11 and2017-18; gold7 uses starting years, while6 can use end2011 to start2017. Reference endpoints not specified.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-69884ef125dd0dab34b52329` · history_3992：Kit sponsors BSR Housewares became the clubs first shirt English football sponsorship during the 1981–82 season. The clubs shirts have been sponsored for the majority of the time since then, although there was no shirt sponsor at the end of the 1993–94 season,…

## 058 · drop · drop:history_4002:3ae4251e-8fde-49d9-84b1-bb0ec5c57c2a:5b6c0e9e7237

**原题：** Which countries had 20% or more of exports?

**原金标：** `[["Italy", "France"], ["France", "Italy"], ["Italy", "France"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** Question does not identify Tunisia or the export source/year among the global corpus.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-e577729ff5568918c4ae8257` · history_4002：The monetary unit is the dinar, at about 1.33 per dollar U.S.A. (recently a fairly constant rate), with inflation estimated pp. 4.5% for 2006. Tunisias per capita annual income was approximately 8,900 dollars (U.S.A.) in 2006. Between 1988 and 1998 the economy…

## 059 · drop · drop:history_4011:b3b9d628-b629-4bcf-8df6-ebe6a19505d2:3749bb1d2528

**原题：** How many more percent of people were Haitian compared to those who were either Jamaican or Bahamian?

**原金标：** `[["3.6"], ["3.2"], ["3.6"]]`；原标可答。

**问题类型：** `gold_error`, `scope_dependent`。

**裁定：** Original4.4-(0.4+0.4)=3.6; spurious3.2 gold is unsupported, and4.0 subtracts only one subgroup. The question additionally omits the locality/year for the ancestry percentages; original 3.6% calculation is context-specific.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-e6a1122c5dea0ba083ab50f4` · history_4011：, those of African ancestry accounted for 19.2% of Miamis population. Out of the 19.2%, 5.6% were West Indian or Afro-Caribbean American origin (4.4% Haitian people, 0.4% Jamaican people, 0.4% Bahamian people, 0.1% British West Indian, and 0.1% Trinidadian and…

## 060 · drop · drop:history_405:f161d073-de40-4b01-ba10-f125c882b53b:8daf164df476

**原题：** Over how many years did Orlogsværftet produce 8 OV-flying boats?

**原金标：** `[["3"], ["3"], ["1915"]]`；原标可答。

**问题类型：** `gold_error`。

**裁定：** 1914-1917 is3 years; extra gold1915 is not a duration. Four calendar production years is a different counting convention.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-5a309f10c81bee380bf7315b` · history_405：Since 1912 the Royal Danish Navy has operated aircraft either as a functional part of the navy or as flights conducted by the Royal Danish Air Force. Aviation pioneer Robert Svendsen purchased the first aircraft, a Henry Farman, and gave it to the navy who sub…

## 062 · drop · drop:history_488:7bbc2a9e-51a9-41c8-beeb-1549d45dee49:3c10d61ee138

**原题：** How many years did these events span?

**原金标：** `[["4"], ["4"], ["4"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** Question refers to these events but does not name any event; global corpus has no unique referent.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-df96c88d606ea80574d7bd3f` · history_488：Paraguay has a small tourism industry. Total tourism receipts declined annually from 2000 through 2002. In 2003 Paraguay's hotel occupancy rate was 38 percent. It increased by 15 percent in 2004. Small gains in tourism have come from business rather than leisu…

## 065 · drop · drop:history_544:c694a9c1-0eb0-4bc5-a07b-df2681a015d4:402f71d7fe94

**原题：** What two important things is in Finland?

**原金标：** `[["orthodox monasteries", "Orthodox Church Museum"], ["monasteries", "Church Museum"], ["Orthodox Church Museum", "The Diocese of Karelia"], ["The Orthodox Church Museum", "The Diocese of Karelia"]]`；原标可答。

**问题类型：** `scope_dependent`, `subjective_selection`。

**裁定：** The unspecified two important things in Finland do not identify a unique subject or selection criterion.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-1b17b3470ce4273a13c47006` · history_544：The seat of the Archbishop of Karelia and All Finland is in Kuopio. The archbishop is the head of the church and the diocese. He is assisted in the diocese by a suffragan bishop known as the Bishop of Joensuu. Despite his title, the bishop is also seated at Ku…

## 067 · drop · drop:history_586:5e2528e0-6623-4116-ac3f-fcab3baaa874:e6f87a91b18a

**原题：** Which tribes were against the Fox?

**原金标：** `[["Illinois", "Ottawas", "Ojibwas", "Huron"], ["Illinois, Ottawas, Ojibwas, and Huron"], ["Illinois, Ottawas, Ojibwas, and Huron"]]`；原标可答。

**问题类型：** `relation_scope_ambiguity`。

**裁定：** French support identifies four tribes opposing the Fox; refusal of sanctuary by Sioux and Iowas is a different relation.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-03460e1324ab788b58928b26` · history_586：For the Fox, the origins, and the potential solution, to resolve their conflict lay in the slave trade. The Foxes were still willing to return to the French alliance if they could secure the return of their captives.:215 In fact, all they wanted was to be cons…

## 071 · drop · drop:history_671:3af750b3-e036-4628-a74a-9665ced01726:03e76b7eeeda

**原题：** How many years was George Downham married to Jaél de Peigne before his death?

**原金标：** `[["15"], ["15"], ["15"], ["15"]]`；原标可答。

**问题类型：** `pronoun_reference_error`, `gold_error`。

**裁定：** The source gives Jael’s death circa 1632, whereas the question asks for George Downham’s death. Gold 15 uses her death.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-b1e1e20edfbeb4bdcca57f04` · history_671：Killigrew lived in Hanworth in Middlesex and Falmouth in Cornwall. On 4 November 1566 Killigrew married in the church of St Peter Le Poer, London, Catherine, fourth daughter of Sir Anthony Cooke. He thus became Cecil's brother-in-law. His wife died in 1583, an…

## 072 · drop · drop:history_671:590583c4-d55c-4428-81c0-ce53eb7d9485:ec8b2d8bc4a9

**原题：** How many years after Killigrew married Jaél de Peigne did she become naturalised?

**原金标：** `[["11"], ["11"], ["11"], ["11"], ["11"], ["11"], ["11"]]`；原标可答。

**问题类型：** `temporal_precision_ambiguity`。

**裁定：** November 1590 to June 1601 is 10 completed years but 11 calendar-year differences; the question does not specify the convention.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-b1e1e20edfbeb4bdcca57f04` · history_671：Killigrew lived in Hanworth in Middlesex and Falmouth in Cornwall. On 4 November 1566 Killigrew married in the church of St Peter Le Poer, London, Catherine, fourth daughter of Sir Anthony Cooke. He thus became Cecil's brother-in-law. His wife died in 1583, an…

## 075 · drop · drop:history_733:81775409-93e5-444f-9368-045419f0acbc:adf5ef26b3ae

**原题：** How many Civil War veterans are listed?

**原金标：** `[["7"], ["7"], ["7"], ["7"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** The question asks how many veterans are listed without identifying the list or material.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-22ce7f0d262d29fafd7da9d2` · history_733：As with most civil wars, the internecine conflict left a bitter legacy, which continues to influence Irish politics to this day. The two largest political parties in the republic through most of its history were Fianna Fáil and Fine Gael, the descendants respe…

## 076 · drop · drop:history_835:a306f4bc-a55e-4b07-89ac-a83876956d97:2c8289ea5e52

**原题：** What happened earlier, the Kiev uprising of 1068 or the Battle of the Alta River?

**原金标：** `[["Battle of the Alta River"], ["Battle of the Alta River"], ["The Kiev uprising of 1068"]]`；原标可答。

**问题类型：** `gold_error`。

**裁定：** The source says the Kiev uprising followed defeat at the Battle of Alta River; gold also incorrectly accepts the uprising as earlier.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-c78939e1a453457ef873fe67` · history_835：The Kiev uprising of 1068 was a revolt against Grand Prince Iziaslav Yaroslavich of Kiev in the aftermath of a Kievan Rus' defeat at the hands of the Cumans at Battle of the Alta River near the city of Pereiaslavl, southeast of Kiev. The Polovtsy raid of 1068-…

## 078 · drop · drop:history_951:5c0c5dac-1f0a-474f-a837-a78a100cb10b:ce22a44f9871

**原题：** How many total casualties did the British suffer, both lost and wounded?

**原金标：** `[["169"], ["169"], ["169"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** British losses and wounded cannot be totaled without identifying the battle or period.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-1453da3d3743cc8e86932373` · history_951：According to official records, the British government lost 43 troops with 126 wounded, while 2337 rebels were killed, another 1652 injured and 45,404 imprisoned. Unofficial estimates put the number at 10,000 killed and 50,000 imprisoned, of who 20,000 were dep…

## 079 · drop · drop:history_972:0a1e6fa5-aa0f-4568-bab1-04f41220c589:7ba1513bcd26

**原题：** Which army was bigger, the Ducth or the French?

**原金标：** `[["French"], ["French"], ["French"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** Which army was bigger depends on the battle and year; neither is given.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-6c87b6116abf50efafb9044d` · history_972：Instead, the Allies spent 1702 and 1703 taking towns such as Venlo and Roermond in the modern Dutch province of Limburg, as well as Liège, Bonn and Huy. Failure to take Antwerp in May 1703 ended hopes of a quick victory while a Dutch army under Slangenburg was…

## 080 · drop · drop:history_972:1511a215-cc80-441c-a962-d8c9458bd461:3055d13b1073

**原题：** Which towns did the Allies take in 1702 and 1703?

**原金标：** `[["Venlo", "Roermond", "Liège", "Bonn", "Huy"], ["Venlo", "Roermond"], ["Venlo", "Roermond", "Liège", "Bonn", "Huy"]]`；原标可答。

**问题类型：** `gold_incomplete`。

**裁定：** The source lists five towns in 1702–1703; the gold also accepts an incomplete two-town alternative.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-6c87b6116abf50efafb9044d` · history_972：Instead, the Allies spent 1702 and 1703 taking towns such as Venlo and Roermond in the modern Dutch province of Limburg, as well as Liège, Bonn and Huy. Failure to take Antwerp in May 1703 ended hopes of a quick victory while a Dutch army under Slangenburg was…

## 081 · drop · drop:history_972:2774b81f-516f-4fcb-be76-c25af051756d:bb3e224b47f0

**原题：** How many years did the Allies fight the Dutch?

**原金标：** `[["2"], ["2"], ["2"]]`；原标可答。

**问题类型：** `relation_premise_error`, `gold_error`。

**裁定：** The cited Dutch army belonged to the Allies; the source does not say Allies fought the Dutch. Gold counts years of Allied campaigns against French-held towns.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-6c87b6116abf50efafb9044d` · history_972：Instead, the Allies spent 1702 and 1703 taking towns such as Venlo and Roermond in the modern Dutch province of Limburg, as well as Liège, Bonn and Huy. Failure to take Antwerp in May 1703 ended hopes of a quick victory while a Dutch army under Slangenburg was…

## 082 · drop · drop:history_990:10ae6922-1c8d-4a74-b236-6b42dd793489:25e9a28f3aab

**原题：** The most serious rebellion was by who?

**原金标：** `[["lord of Yamethin"], ["Toungoo"], ["lord of Yamethin"], ["lord of Yamethin"]]`；原标可答。

**问题类型：** `scope_dependent`, `gold_error`。

**裁定：** The king/rebellion is not identified in the question; gold conflates the most serious rebellion against Minkhaung II (Yamethin) with the chronically troublesome Toungoo.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-607b8ba694ac6b0ffc7f6c4a` · history_990：After the end of Forty Years' War in 1424, Ava gave up its dream of rebuilding the Pagan Empire. Instead, for the next six decades, it would struggle to hold on to its vassal states. Rebellions arose every time a new king came to power. The new king would have…

## 084 · drop · drop:nfl_1021:45ea2538-8a4c-4698-99c0-34af1a5f6fd2:f5d4be1f87bf

**原题：** How many yards difference was Matt Prater’s first field goal compared to his second field goal?

**原金标：** `[["17"], ["17"], ["17"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** The Matt Prater game/date is unspecified, so the first and second field goals have no unique global referent.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-ad0a2517b61394ea879fd4d7` · nfl_1021：The Broncos began their 2008 campaign on the road against their AFC West rival, the Oakland Raiders, in the second game of ESPN's Monday Night Football doubleheader. In the first quarter, Denver ran out of the gates early as QB Jay Cutler completed a 26-yard T…

## 085 · drop · drop:nfl_1195:5c5d64d5-53f8-4388-9c27-f084daadabd4:811b40168e3a

**原题：** How many safeties were scored?

**原金标：** `[["1"], ["1"], ["1"], ["1"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** Game/season/material is unspecified; a game-specific count, extreme, scorer or venue has no unique scope in the pooled corpus.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-649e3e8077c75af912979d72` · nfl_1195：The Eagles faced off with the Baltimore Ravens in an interconference matchup. Both defenses dominated the first quarter, and in the second quarter, Baltimore forced three turnovers (two interceptions and a fumble) from Donovan McNabb. Ravens kicker Matt Stover…

## 086 · drop · drop:nfl_1329:f018bbae-ee33-4d2c-a81c-e57d9a9594f2:6a68277138a1

**原题：** How many touchdowns were scored in the game.

**原金标：** `[["5"], ["5"], ["5"], ["5"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** Game/season/material is unspecified; a game-specific count, extreme, scorer or venue has no unique scope in the pooled corpus.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-c69a6e78ac32821e03539af8` · nfl_1329：Coming off their impressive road win over the 49ers, the Falcons went home for a Week 6 Sunday night duel with the Chicago Bears. After a scoreless first quarter, Atlanta would trail early in the second quarter as Bears quarterback Jay Cutler found wide receiv…

## 087 · drop · drop:nfl_1344:730567fe-5bab-43e6-80df-ccda58d4134f:b4801fbd7d69

**原题：** Who on Baltimore scored first?

**原金标：** `[["Joe Flacco"], ["Derrick Mason"], ["Derrick Mason"]]`；原标可答。

**问题类型：** `scope_dependent`, `reference_ambiguity`。

**裁定：** Game/season/material is unspecified; a game-specific count, extreme, scorer or venue has no unique scope in the pooled corpus. The reference also contains competing scorers/counts or a non-equivalent alternative.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-867558a0f0601b346d25fa66` · nfl_1344：Coming off their divisional home win over the Browns, the Ravens flew to Gillette Stadium for a Week 4 battle with the New England Patriots. After Patriots kicker Stephen Gostkowski got a 32-yard field goal in the first quarter, Baltimore got into the game wit…

## 089 · drop · drop:nfl_1689:ee81fe68-d7a7-444e-8625-ee256ecd0fbf:c3e77537ddf8

**原题：** How many yards was the longest touchdown run?

**原金标：** `[["58-yard"], ["58"], ["58"], ["58"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** Game/season/material is unspecified; a game-specific count, extreme, scorer or venue has no unique scope in the pooled corpus.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-557dec2125bf699297e5a9f3` · nfl_1689：The Redskins traveled to the Georgia Dome to play the Atlanta Falcons. Washington trailed in the first quarter as Falcons quarterback Matt Ryan found tight end Tony Gonzalez on a 2-yard touchdown pass, followed by cornerback Tye Hill returning an interception …

## 090 · drop · drop:nfl_1914:31116808-3351-443e-8852-5b94a0f17dca:ec7b7428de15

**原题：** How many field goals were made?

**原金标：** `[["4"], ["4"], ["4"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** Game/season/material is unspecified; a game-specific count, extreme, scorer or venue has no unique scope in the pooled corpus.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-b1a6e0878dceb47d81c02aee` · nfl_1914：Coming off their win over the Vikings, the Dolphins went home, donned their alternate uniforms, and played their Week 3 home-opener against their AFC East foe, the New York Jets, on Sunday night. Miami trailed early in the first quarter as Jets quarterback Mar…

## 091 · drop · drop:nfl_2297:acce6054-8e36-4531-843c-aa4ca5d569ae:2e9be237612d

**原题：** Which player scored the longest touchdown of the game?

**原金标：** `[["Percy Harvin"], ["Percy Harvin"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** Game/season/material is unspecified; a game-specific count, extreme, scorer or venue has no unique scope in the pooled corpus.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-f5b5809f480f5a1364155b84` · nfl_2297：Coming off their special teams woes in 2010, the Chargers allowed a 103-yard opening kickoff return to Percy Harvin to start the season. Kicker Nate Kaeding suffered a season-ending injury on the play, and punter Mike Scifres assumed place kicking responsibili…

## 092 · drop · drop:nfl_2385:ee89c8b0-506a-4f94-b343-8b7e3c761e00:9de7540d422e

**原题：** How many touchdowns were passing touchdowns?

**原金标：** `[["2"], ["2"], ["2"], ["2"], ["2"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** Game/season/material is unspecified; a game-specific count, extreme, scorer or venue has no unique scope in the pooled corpus.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-b7d656ece28cd8c5cee1eb41` · nfl_2385：In week 9, the Lions flew south to play the Jacksonville Jaguars. After a scoreless first quarter, the Lions' Mikel Leshoure ran in three consecutive touchdowns, from 7, 1, and 8 yards out respectively to take a 21-point lead going into halftime. Leshoure beca…

## 093 · drop · drop:nfl_2645:ec9a30cd-60aa-4738-aa8d-54be7aff8484:23b4dd7e9b17

**原题：** How many field goals did Bironas kick in the fourth quarter?

**原金标：** `[["2"], ["2"], ["2"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** Game/season/material is unspecified; a game-specific count, extreme, scorer or venue has no unique scope in the pooled corpus.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-09ae237d77410b3ed111eb2b` · nfl_2645：The Steelers started their 2013 season at home against the Titans. They scored first when Darius Reynaud down the ball in their own end zone to give the Steelers a safety for a 2-0 lead in the first quarter. In the 2nd quarter, the Titans took the lead as Jack…

## 095 · drop · drop:nfl_3285:463663ae-90b1-4ef3-ad30-9e760fcaacac:2a5760913490

**原题：** Which two players made the score 10-0 in the second quarter?

**原金标：** `[["Blake Bortles", "Marqise Lee"], ["Blake Bortles", "Marqise Lee"], ["Blake Bortles", "Marqise Lee"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** Game/season/material is unspecified; a game-specific count, extreme, scorer or venue has no unique scope in the pooled corpus.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-30d490accc2dc3ce4dd6f51d` · nfl_3285：After winning at home, the Bengals traveled down south to take on the Jaguars. The Jags scored first in the first quarter when Josh Lambo kicked a 32-yard field goal to make it 3-0. They would make it 10-0 in the second quarter when Blake Bortles found Marqise…

## 096 · drop · drop:nfl_336:398635c8-892f-4777-a316-7843c573322b:8f78b2b8221d

**原题：** How many field goals were kicked in the first quarter?

**原金标：** `[["2"], ["2"], ["2"], ["2"], ["2"], ["1"], ["2"], ["2"]]`；原标可答。

**问题类型：** `scope_dependent`, `reference_ambiguity`。

**裁定：** Game/season/material is unspecified; a game-specific count, extreme, scorer or venue has no unique scope in the pooled corpus. The reference also contains competing scorers/counts or a non-equivalent alternative.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-b2202ae169dfa3b822a02196` · nfl_336：The Chargers at home, donned their throwback jerseys and played a Week 9 fight against the Cleveland Browns. In the first quarter, both sides would limit each other to a field goal. Chargers kicker Nate Kaeding would complete a 29-yard field goal, while Browns…

## 097 · drop · drop:nfl_496:39592c9a-2865-4048-af3d-3c041e7ce83b:ceb879b3f400

**原题：** Which team scored the first touchdown?

**原金标：** `[["Cardinals"], ["Cardinals"], ["the Panthers"], ["Cardinals"], ["Cardinals"], ["Cardinals"]]`；原标可答。

**问题类型：** `scope_dependent`, `reference_ambiguity`。

**裁定：** Game/season/material is unspecified; a game-specific count, extreme, scorer or venue has no unique scope in the pooled corpus. The reference also contains competing scorers/counts or a non-equivalent alternative.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-d387c7521ba25635a1286dcd` · nfl_496：Coming off their divisional road win over the Saints, the Panthers flew to the University of Phoenix Stadium for a Week 6 throwdown with the Arizona Cardinals With David Carr recovering from injuries, recently signed QB Vinny Testaverde got the start. In the f…

## 098 · drop · drop:nfl_649:a857aeb6-b343-4b94-b220-9c124629e317:f030f456d074

**原题：** How long was the longest field goal?

**原金标：** `[["48-yard"], ["48-yard"], ["48-yard"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** Game/season/material is unspecified; a game-specific count, extreme, scorer or venue has no unique scope in the pooled corpus.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-787cdb293c89b376eeaa375d` · nfl_649：Coming off of their bye week, the Chiefs were at home for a Week 9 interconference duel with the Green Bay Packers. After a scoreless first quarter, Kansas City trailed early on in the second quarter with Packers kicker Mason Crosby getting a 48-yard and a 36-…

## 099 · drop · drop:nfl_663:6b5ac99f-05d1-4821-a109-10ff81c5b77d:f08fdfb2bcdb

**原题：** What are the top two longest field goals made?

**原金标：** `[["43-yard", "40-yard"], ["43-yard", "40-yard"], ["43-yard", "40-yard"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** Game/season/material is unspecified; a game-specific count, extreme, scorer or venue has no unique scope in the pooled corpus.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-29f9db3f5047e25f2539ca12` · nfl_663：Still looking for their first win of the year, the Dolphins flew to Cleveland Browns Stadium for a Week 6 intraconference duel with the Cleveland Browns. With QB Trent Green still suffering from a concussion from last week, back-up QB Cleo Lemon was given the …

## 100 · drop · drop:nfl_669:35340705-28a9-4165-a5c2-3cb3e3023e82:7c64ee22beca

**原题：** How many yards longer was the first field goal compared to the third?

**原金标：** `[["24"], ["24"], ["24"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** Game/season/material is unspecified; a game-specific count, extreme, scorer or venue has no unique scope in the pooled corpus.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-4e50a8bf31b93030456f4b03` · nfl_669：Still trying to get their first win of the year, the Dolphins went home for a Week 13 AFC East rematch with the throwback-clad New York Jets. In the first quarter, Miami trailed early as Jets RB Leon Washington got an 18-yard TD run. The Dolphins ended the per…

## 102 · drop · drop:nfl_719:515ca86a-33cd-4336-bed4-d8d95e7345b2:113c9578bb25

**原题：** How many touchdowns did Wynn score?

**原金标：** `[["1"], ["1"], ["1"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** Game/season/material is unspecified; a game-specific count, extreme, scorer or venue has no unique scope in the pooled corpus.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-f555b29308ac625eb431d197` · nfl_719：The Giants played their Week 2 home opener against the Green Bay Packers. After both teams missed a field goal to open the game, Green Bay got on the board first with a 6-yard run by rookie DeShawn Wynn. The Giants responded with a 26-yard scoring strike by El…

## 103 · drop · drop:nfl_749:634ca04c-e1fa-4f8d-a589-ddac1739d124:1a104e722a1d

**原题：** How many field goals did each kicker score in the first half?

**原金标：** `[["1"], ["1"], ["1"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** Game/season/material is unspecified; a game-specific count, extreme, scorer or venue has no unique scope in the pooled corpus.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-b3db5bed86f7ede5ebf867ab` · nfl_749：Hoping to rebound from their embarrassing home loss to the Lions, the Raiders flew to Invesco Field at Mile High for an AFC West duel with the Denver Broncos. In the first quarter, Oakland trailed early as Broncos QB Jay Cutler completed a 9-yard TD pass to WR…

## 104 · drop · drop:nfl_858:5db7b970-8c1c-4701-9e08-49b6952612a2:16a00383dff4

**原题：** What's the name of the field the game was played on?

**原金标：** `[["Raymond James Stadium"], ["Raymond James Stadium"], ["Raymond James Stadium"], ["Raymond James Stadium"], ["Raymond James Stadium"], ["Raymond James Stadium"], ["Raymond James Stadium"], ["Raymond James Stadium"], ["Raymond James Stadium"], ["Raymond James Stadium"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** Game/season/material is unspecified; a game-specific count, extreme, scorer or venue has no unique scope in the pooled corpus.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-a99dbf5835c0599fc1aed482` · nfl_858：The Buccaneers hosted the Titans at Raymond James Stadium, their second inter-conference matchup in two weeks. Buccaneers running back Earnest Graham was a part of the starting lineup for the first time in his career, replacing the injured Michael Pittman, who…

## 105 · drop · drop:nfl_915:fbddf60a-31e4-4850-be04-44c4871079a3:d90213b8b036

**原题：** In which two quarters did Stephen Gostkowski only kick one field goal?

**原金标：** `[["second", "fourth"], ["second quarter", "fourth quarter"], ["the second", "the fourth"], ["2"], ["second", "fourth"], ["2"]]`；原标可答。

**问题类型：** `scope_dependent`, `reference_ambiguity`。

**裁定：** Game/season/material is unspecified; a game-specific count, extreme, scorer or venue has no unique scope in the pooled corpus. The reference also contains competing scorers/counts or a non-equivalent alternative.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-79e72cdb02ef1c74f9842a2e` · nfl_915：Hoping to rebound from their home loss to the Vikings, the Cardinals flew to Gillette Stadium for a Week 16 interconference duel with the New England Patriots. Arizona would trail early in the first quarter as Patriots running back LaMont Jordan got a one-yard…

## 106 · drop · drop:nfl_995:08c59a80-401a-446b-966b-7a87818b20f4:a337b43cd7cc

**原题：** How many touchdowns did T.J. Houshmandzadeh have?

**原金标：** `[["2"], ["2"], ["2"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** Game/season/material is unspecified; a game-specific count, extreme, scorer or venue has no unique scope in the pooled corpus.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-9e005ef22a16b773c36ccdeb` · nfl_995：Still searching for their first win, the Bengals flew to Texas Stadium for a Week 5 interconference duel with the Dallas Cowboys. In the first quarter, Cincinnati trailed early as Cowboys kicker Nick Folk got a 30-yard field goal, along with RB Felix Jones get…

## 109 · hotpot · hotpot:5a74be7a55429929fddd84e4

**原题：** What is served shaken with ice and should not be mistaken with the name Margarita?

**原金标：** `[["A margarita"]]`；原标可答。

**问题类型：** `relation_premise_error`。

**裁定：** The clue “not mistaken for Margarita” describes the name Margarida, while “shaken with ice” describes Margarita itself. Combining them produces a contradictory target.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-eb3db6fe91cae2cd20e5497c` · Margarida：It should not be mistaken for the Mexican alcoholic drink Margarita or the Italian Margherita pizza.
- 原支持 `s-cb94b07de8971e78f01724a2` · Margarita：A margarita is a cocktail consisting of tequila, triple sec, and lime or lemon juice, often served with salt or sugar on the rim of the glass.
- 原支持 `s-660c712dbde198fb32912bca` · Margarita：The drink is served shaken with ice (on the rocks), blended with ice (frozen margarita), or without ice (straight up).

## 125 · hotpot · hotpot:5a7c76275542990527d554b4

**原题：** Which film was released first out of The Hunchback of Notre Dame and Miracle of the White Stallions?

**原金标：** `[["Miracle of the White Stallions"]]`；原标可答。

**问题类型：** `entity_version_ambiguity`。

**裁定：** The question omits the 1996 version qualifier for The Hunchback of Notre Dame, which has earlier film versions that reverse the comparison.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-5effa8288ae7e168a4999f19` · The Hunchback of Notre Dame (1996 film)：The Hunchback of Notre Dame is a 1996 American animated musical drama film produced by Walt Disney Feature Animation for Walt Disney Pictures.
- 原支持 `s-d773c126fbffd2700bd3d548` · Miracle of the White Stallions：Miracle of the White Stallions is a 1963 film released by Walt Disney starring Robert Taylor (playing Alois Podhajsky), Lilli Palmer, and Eddie Albert.

## 135 · hotpot · hotpot:5a84a4e35542992a431d1a81

**原题：** Does Jimmy Barnes have more hits, both solo and in a group, than Yang Seung-ho has with his group?

**原金标：** `[["yes"]]`；原标可答。

**问题类型：** `insufficient_reference_comparison`。

**裁定：** The source states Barnes’s hit-album count but gives no comparable hit count for MBLAQ. Absence of a number for Yang does not establish fewer hits.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-69ea75db392615628275d0b5` · Yang Seung-ho：Yang Seung-ho (양승호; born October 16, 1987), is a South Korean idol singer, dancer, actor, model and the leader of Korean male group MBLAQ, currently active in South Korea under Will Entertainment.
- 原支持 `s-6e6d4e08dc707d00fedad94a` · Jimmy Barnes：James Dixon Swan {'1': ", '2': ", '3': ", '4': "} (born 28 April 1956), better known as Jimmy Barnes, is a Scottish-Australian rock singer-songwriter.
- 原支持 `s-4b5483d091429d21637e9c7f` · Jimmy Barnes：His career both as a solo performer and as the lead vocalist with the rock band Cold Chisel has made him one of the most popular and best-selling Australian music artists of all time.
- 原支持 `s-a5d8b8c50a91bddd975bfb3b` · Jimmy Barnes：The combination of 14 Australian Top 40 albums for Cold Chisel and 13 charting solo albums, including nine No. 1s, gives Barnes the highest number of hit albums of any Australian artist.

## 137 · hotpot · hotpot:5a8656ca554299211dda2afc

**原题：** Which black comedy sequel to "Forgetting Sarah Marshall" starred an Australian actress?

**原金标：** `[["Rose Byrne"]]`；原标可答。

**问题类型：** `gold_target_type_error`。

**裁定：** The question asks which film; correct film is Get Him to the Greek, whereas gold names actress Rose Byrne.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-648e3eb13dd87703d2ce1475` · Get Him to the Greek：Released on June 4, 2010, the film serves as a spin-off sequel of Stoller's 2008 film "Forgetting Sarah Marshall", reuniting director Stoller with stars Hill and Brand and producer Judd Apatow.
- 原支持 `s-0c87ec2c8c17f0cf1218d15f` · Rose Byrne：Mary Rose Byrne (born 24 July 1979) is an Australian actress.
- 原支持 `s-cc082083054d848461ea5395` · Rose Byrne：Byrne has also starred in the films "Troy", "28 Weeks Later", "Knowing", "Insidious", "", "", as well as the comedies "Get Him to the Greek", "Bridesmaids", "Neighbors", "", and "Spy."

## 138 · hotpot · hotpot:5a88a23f5542997e5c09a652

**原题：** Which director, Chris O'Dea or Lucky McKee,  is a Master of Fine Arts graduate of the UCLA School of Theater, Film and Television?

**原金标：** `[["Edward Lucky McKee"]]`；原标可答。

**问题类型：** `gold_error`。

**裁定：** Source explicitly gives the UCLA MFA to Chris O’Dea, not Lucky McKee.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-3fce0046b4526098cd485733` · Chris O'Dea：Chris O'Dea is a documentary filmmaker with a focus on new media and global perspectives.
- 原支持 `s-c37b3b5388be043f220dae22` · Chris O'Dea：He is a Master of Fine Arts graduate of the UCLA School of Theater, Film and Television.
- 原支持 `s-41c9223e5d8ecfdba79e8f33` · Lucky McKee：Edward Lucky McKee (born November 1, 1975) is an American director, writer, and actor, largely known for the cult 2002 film "May".

## 143 · hotpot · hotpot:5a8ceb4955429941ae14df43

**原题：** What was the nickname of the player who played his final season for the 2016 Miami Dolphins before dying in an accident?

**原金标：** `[["Niño"]]`；原标可答。

**问题类型：** `entity_premise_error`。

**裁定：** The question says Miami Dolphins (NFL), but the reference is José Fernández’s 2016 Miami Marlins (baseball) season.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-d5d66d1bea5cb6493ff30e96` · 2016 Miami Marlins season：This also marked José Fernández's final season as he died in a boating accident on September 25.
- 原支持 `s-1a9934c2f8535965ac41359a` · José Fernández (pitcher)：He was affectionately known as "Niño" by his teammates and fans due to the youthful exuberance with which he played the game of baseball.

## 145 · hotpot · hotpot:5a8e3dc05542995085b37401

**原题：** Which case was decided first, Selle v. Gibb or Reynolds v. Sims?

**原金标：** `[["1964"]]`；原标可答。

**问题类型：** `gold_target_type_error`。

**裁定：** Question requests a case, correctly Reynolds v. Sims; gold supplies only its year 1964.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-f83a14fcad247c94dd4e86b5` · Reynolds v. Sims：Reynolds v. Sims, 377 U.S. 533 (1964) was a United States Supreme Court case that ruled that unlike in the election of the United States Senate, in the election of "any" chamber of a state legislature the electoral districts must be roughly equal in population…
- 原支持 `s-b45c12945cd46abbce49c65f` · Selle v. Gibb：Selle v. Gibb, 741 F.2d 896 (7th Cir.
- 原支持 `s-4b5c31ec7af642ce98f51ae2` · Selle v. Gibb：1984) was a landmark ruling on the doctrine of striking similarities.

## 162 · hotpot · hotpot:5ab97b27554299131ca4233c

**原题：** What characteristics do Rosario Castellanos and Arthur Hailey  share?

**原金标：** `[["poet and author."]]`；原标可答。

**问题类型：** `gold_error`。

**裁定：** Both are authors/novelists; the source identifies Castellanos as a poet but does not establish Hailey as a poet. Gold overstates their shared characteristics.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-a3ed19caf19d743655b75480` · Rosario Castellanos：Rosario Castellanos Figueroa (] ; 25 May 1925 – 7 August 1974) was a Mexican poet and author.
- 原支持 `s-9a5b7ef18be26804965c70b6` · Arthur Hailey：Arthur Hailey (April 5, 1920 – November 24, 2004) was a British-Canadian novelist whose plot-driven storylines were set against the backdrops of various industries.

## 164 · hotpot · hotpot:5abad70d5542996606241631

**原题：** How high on the US "Billboard" Hot 100 chart did Dan Smith's song reach in 2013?

**原金标：** `[["number 5"]]`；原标可答。

**问题类型：** `temporal_reference_ambiguity`。

**裁定：** Sources date the song’s release/popularity to 2013 but do not date its US chart peak to 2013; the question attaches 2013 to reaching the rank.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-b582ecbc8b9f0c0d86c088fa` · Dan Smith (singer)：Daniel Campbell "Dan" Smith (born 14 July 1986) is a British singer-songwriter and record producer.
- 原支持 `s-39a92fb882bd7efe1f07ffc7` · Dan Smith (singer)：He is the lead singer, primary songwriter.
- 原支持 `s-90b5ef50d9680859bb8e141a` · Dan Smith (singer)：The band formed in 2010 and gained mass popularity in 2013 when the song "Pompeii" was released.
- 原支持 `s-31199d34fe2667cf85bbade5` · Pompeii (song)：The song was released as the album's fourth single on 12 January 2013.
- 其余 1 个证据单元见完整 JSON 账本。

## 167 · hotpot · hotpot:5abfa5fb5542990832d3a183

**原题：** Where is the  city and "comune" in Emilia-Romagna in which talian Renaissance painter ;Ludovico Mazzolino located

**原金标：** `[["northern Italy,"]]`；原标可答。

**问题类型：** `question_corruption`。

**裁定：** The sentence omits the painter’s relation and garbles the location target. Ferrara identifies the city; gold northern Italy identifies its location.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-e407739786a2568dfe31874a` · Ferrara：Ferrara (] ) (Emilian: Frara) is a city and "comune" in Emilia-Romagna, northern Italy, capital city of the Province of Ferrara.
- 原支持 `s-9056db9e245af76a9f1f5149` · Ludovico Mazzolino：Ludovico Mazzolino (1480 – c. 1528) - also known as Mazzolini da Ferrara, Lodovico Ferraresa, and Il Ferrarese - was an Italian Renaissance painter active in Ferrara and Bologna.

## 171 · hotpot · hotpot:5ac1b8cd55429963665198fc

**原题：** Who won more awards, Brothers Quay or Jake Kasdan?

**原金标：** `[["Brothers Quay"]]`；原标可答。

**问题类型：** `insufficient_reference_comparison`。

**裁定：** Reference gives one Quay award but no comparable Kasdan award count; missing mentions do not establish who won more.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-ee2deb686fc2834d5712373c` · Jake Kasdan：Jacob "Jake" Kasdan (born October 28, 1974) is an American television and film director and occasional actor.
- 原支持 `s-0eb1664229961c031d64841f` · Brothers Quay：Stephen and Timothy Quay ( ; born June 17, 1947) are American identical twin brothers better known as the Brothers Quay or Quay Brothers.
- 原支持 `s-fad0f8f9ff89a871bf5e91aa` · Brothers Quay：They are also the recipients of the 1998 Drama Desk Award for Outstanding Set Design for their work on the play "The Chairs".

## 174 · hotpot · hotpot:5ac3d2ac554299741d48a330

**原题：** What has Henry Winkler worked as that Norman Foster has not?

**原金标：** `[["comedian"]]`；原标可答。

**问题类型：** `gold_incomplete`, `entity_ambiguity`。

**裁定：** Comedian, producer and author all satisfy the given actor/director reference. Gold accepts only comedian; Norman Foster also names an architect in the corpus.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-5a567f7a5cecd4e3cd1f7f8e` · Henry Winkler：Henry Franklin Winkler (born October 30, 1945) is an American actor, director, comedian, producer, and author.
- 原支持 `s-36eba1c1573439e040f7179c` · Norman Foster (director)：Norman Foster (born Norman Foster Hoeffer, December 13, 1903 – July 7, 1976) was an American actor, film director and screenwriter.

## 195 · hotpot · hotpot:5ae22895554299495565da0c

**原题：** The Battle of the Catalaunian Plains involved the king of the nomadic tribes of which peoples?

**原金标：** `[["Germanic"]]`；原标可答。

**问题类型：** `entity_reference_ambiguity`。

**裁定：** The battle description names both Visigothic king Theodoric and Hunnic king Attila. “King of nomadic tribes” does not uniquely specify which side; Huns and Germanic/Visigothic readings differ.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-2f04c6d9a9ea17a5f22a463c` · Visigoths：The Visigoths ( ; ; Latin: "Visigothi", "Wisigothi", "Vesi", "Visi", "Wesi", or "Wisi" ; Italian: "Visigoti" ) were the western branches of the nomadic tribes of Germanic peoples referred to collectively as the Goths.
- 原支持 `s-97b2cab177ea084efb91d1da` · Battle of the Catalaunian Plains：The Battle of the Catalaunian Plains (or Fields), also called the Battle of the Campus Mauriacus, Battle of Châlons or the Battle of Maurica, took place on June 20, 451 AD, between a coalition led by the Roman general Flavius Aetius and the Visigothic king The…

## 197 · hotpot · hotpot:5ae4b5935542995ad6573e27

**原题：** Which of these artist lives closer to Canada, Koshi Inaba or Jonathan Davis?

**原金标：** `[["Jonathan Howsmon Davis"]]`；原标可答。

**问题类型：** `residence_not_established`。

**裁定：** Sources give birthplace/nationality, not where the artists live, so they cannot establish the requested residential distance to Canada.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-57ec130c84167bcafbd30be2` · Koshi Inaba：Koshi Inaba (稲葉浩志 , Inaba Kōshi ) , born Hiroshi Inaba (稲葉浩志 , Inaba Hiroshi ) on September 23, 1964 in Tsuyama, Okayama, is a Japanese vocalist, multi-instrumentalist and songwriter.
- 原支持 `s-517e3d9135a5e9ed6dfbedc0` · Jonathan Davis：Jonathan Howsmon Davis (born January 18, 1971), also known as JD and JDevil (or J Devil), is an American musician best known as the leading vocalist and frontman of the nu metal band Korn.

## 200 · hotpot · hotpot:5ae547f85542990ba0bbb254

**原题：** Who was the original recording artist of the song which was chosen over If You've Got Trouble for the Beatles' "Help!" film?

**原金标：** `[["Buck Owens"]]`；原标可答。

**问题类型：** `relation_premise_error`。

**裁定：** Source explicitly says replacement song Act Naturally is not in the Help! film; the question places it in the film.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-168336108bee409e1eef66d1` · If You've Got Trouble：" film, but the Beatles were not happy with the recording and later chose "Act Naturally" (which is not in the film) instead.
- 原支持 `s-f79021ff3628b398f49627a1` · Act Naturally："Act Naturally" is a song written by Johnny Russell and Voni Morrison, originally recorded by Buck Owens and the Buckaroos, whose version reached number 1 on the "Billboard" Country Singles chart in 1963, his first chart-topper.

## 205 · hotpot · hotpot:5ae75a4c5542997b22f6a6f1

**原题：** The Bart the Fink episode of The Simpsons aired in February of 1996, and involves the host of which kiddie show?

**原金标：** `[["The Itchy & Scratchy Show"]]`；原标可答。

**问题类型：** `gold_error`, `reference_containment_error`。

**裁定：** Krusty hosts a kiddie variety show containing The Itchy & Scratchy Show cartoons, rather than hosting that cartoon itself; gold confuses containing show and embedded cartoon.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-27ba15ce1249cabcfa9324de` · Krusty the Clown：He is the long-time clown host of Bart and Lisa's favorite TV show, a combination of kiddie variety television hijinks and cartoons including "The Itchy & Scratchy Show".
- 原支持 `s-9e0824b005c49c2dbf1ff4f0` · Bart the Fink：It originally aired on the Fox network in the United States on February 11, 1996.

## 206 · musique · musique:2hop__104095_40502:unanswerable

**原题：** Before this emigration what was the population of Clara Peeters's country of citizenship?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `scope_dependent`。

**裁定：** “This emigration” has no referent. Original local-context unanswerability is not a verified full-corpus negative.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-30beae5ba1b165d1ad9b51c5` · Arthur Rahn：Leutnant Arthur Rahn (18 July 1897 – 27 April 1962) was a German World War I flying ace credited with six aerial victories. He served as a fighter pilot for about two years before being wounded in action on 27 July 1918. Exiting hospital after war's end, he ma…
- 原上下文 `s-a3a753599cec224b85e9611d` · Wim Peeters：Wim Peeters (22 August 1925 – 15 August 2011) was a South African sports shooter. He competed in the trap event at the 1960 Summer Olympics.
- 原上下文 `s-2bff2135f56a251681c19574` · Summer School Teachers：Summer School Teachers is a 1974 feature film directed and written by Barbara Peeters and starring Candice Rialson. It is about three female friends who all teach at a school over the summer.
- 原上下文 `s-73dbc850aa403da8f6ac9720` · Old Ironsides station：Old Ironsides is a light rail station operated by Santa Clara Valley Transportation Authority (VTA), located in Santa Clara, California. Old Ironsides is served by the Mountain View–Winchester light rail line.
- 其余 16 个证据单元见完整 JSON 账本。

## 209 · musique · musique:2hop__124412_402641:answerable

**原题：** Who is the child of Chiang Hsiao-wen's father?

**原金标：** `[["Chiang Hsiao-wu"], ["Alex Chiang"]]`；原标可答。

**问题类型：** `nonunique_reference`。

**裁定：** Chiang Ching-kuo had several children including the subject; singular gold lists only one valid child and its alias. All named children satisfy the literal relation.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-0fb2371af0ab2fb948d2ae6d` · Chiang Hsiao-wu：Chiang Hsiao-wu (; also known as Alex Chiang; April 25, 1945 - July 1, 1991) was the second son of Chiang Ching-kuo, the President of the Republic of China in Taiwan from 1978 to 1988. His mother is Faina Ipatyevna Vakhreva, also known as Chiang Fang-liang. He…
- 原支持 `s-150c78149e2660d2298933c9` · Chiang Hsiao-wen：Chiang Hsiao-wen (; also known as Alan Chiang; 14 December 1935 – 14 April 1989) was the eldest son of Chiang Ching-kuo, the President of the Republic of China in Taiwan from 1978 to 1988. His mother is Faina Ipatyevna Vakhreva, also known as Chiang Fang-liang…

## 210 · musique · musique:2hop__128420_375952:answerable

**原题：** What county is the city where KQBA is located?

**原金标：** `[["Santa Barbara County"], ["Santa Barbara County, California"]]`；原标可答。

**问题类型：** `gold_entity_conflation`。

**裁定：** KQBA is licensed to Los Alamos, New Mexico; the gold follows unrelated Los Alamos, California to Santa Barbara County. Correct county is Los Alamos County.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-87952358a64c4d28966ad066` · Los Alamos, California：Los Alamos (Spanish for "The Cottonwoods") is a census-designated place (CDP) in Santa Barbara County, California, United States. Although located in the Los Alamos Valley, the town of Los Alamos is usually considered to be a part of the Santa Ynez Valley comm…
- 原支持 `s-09a55b3e12f5bb1be7367035` · KQBA：KQBA (107.5 FM, "Outlaw Country") is an American radio station licensed to Los Alamos, New Mexico, and serving the Santa Fe area and northern New Mexico. It is owned by Hutton Broadcasting and has a country music format.

## 211 · musique · musique:2hop__135668_561444:unanswerable

**原题：** Who is daughter to the third Rebbe of Chabad Lubavitch, named after his father?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `broken_reference_chain`。

**裁定：** The daughter/father naming chain is not established by the supplied reference; a global-corpus negative has not been verified.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-45f18ad2e910953f505209d0` · The Howling (film)：The Howling is a 1981 American horror film directed by Joe Dante and starring Dee Wallace, Patrick Macnee, Dennis Dugan, and Robert Picardo. Based on the novel of the same name by Gary Brandner, the film follows a television newswoman sent to a remote mountain…
- 原上下文 `s-497d78ebea740bc3e7b9cc37` · Hoji Fortuna：Born in Luanda and named after war hero Hoji-ya-Henda, Fortuna emigrated to Portugal at the age of 20. He worked as a model and DJ in parallel to his study of law at the Catholic University of Portugal in Porto, and eventually took up acting. In 2008, he follo…
- 原上下文 `s-7e2bde72ba1dc9deef685e76` · Candy Broad：After making a name for herself as the Assistant National Secretary of the Australian Labor Party (ALP), Broad moved into politics, contesting a safe Labor seat at the 1999 Victorian state election. She was immediately promoted into the ministry and served thr…
- 原上下文 `s-c5315b60b42ee62835cd9c82` · Malaysian names：Following traditional practice from South India, the father's name is sometimes abbreviated to an initial and placed before the personal name. Thus, a man called Anbuselvan whose father is called Ramanan may be called Anbuselvan anak lelaki Ramanan (formal), A…
- 其余 16 个证据单元见完整 JSON 账本。

## 213 · musique · musique:2hop__170341_43945:answerable

**原题：** Why was Common Sense, by the same author as Rights of Man, an important work?

**原金标：** `[["crystallized the rebellious demand for independence from Great Britain"], ["Great Britain"]]`；原标可答。

**问题类型：** `gold_incomplete`。

**裁定：** Gold also accepts bare “Great Britain”, which names the country but does not explain the work’s importance.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-d79cc231ab2e94cb8fd2e775` · Rights of Man：Rights of Man (1791), a book by Thomas Paine, including 31 articles, posits that popular political revolution is permissible when a government does not safeguard the natural rights of its people. Using these points as a base it defends the French Revolution ag…
- 原支持 `s-8bd2bf8e84216e9b6e81eda6` · Thomas Paine：Born in Thetford in the English county of Norfolk, Paine migrated to the British American colonies in 1774 with the help of Benjamin Franklin, arriving just in time to participate in the American Revolution. Virtually every rebel read (or listened to a reading…

## 214 · musique · musique:2hop__203985_524737:unanswerable

**原题：** In what league did Jose Miranda's team compete?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `scope_dependent`。

**裁定：** Jose Miranda/team lacks identifying season or team; original context-local unanswerability cannot establish a full-corpus negative.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-5473a8cc27578ad199a0e155` · Lynbrook High School：Lynbrook High School (also referred to as Lynbrook or LHS) is a co-educational, public, four-year high school located in the West San Jose neighborhood of San Jose, California, USA. It was founded in 1965 and graduated its first class in 1968.
- 原上下文 `s-00f8e58170caa87cc7aaac95` · Haters Back Off：Haters Back Off is a television comedy series starring Colleen Ballinger, based on her character Miranda Sings, that was released on Netflix on October 14, 2016. The ``surreal and absurd ''series centers around the family life of Miranda Sings, a sheltered, se…
- 原上下文 `s-44f6903660ad41781d614faa` · Carmen Miranda：Carmen Miranda GCIH, OMC Portuguese pronunciation: (ˈkaɾmẽȷ̃ miˈɾɐ̃dɐ), born Maria do Carmo Miranda da Cunha (February 9, 1909 -- August 5, 1955), was a Portuguese samba singer, dancer, Broadway actress, and film star who was popular from the 1930s to the 1950…
- 原上下文 `s-9f33b2b1291fac8e7f74f8c9` · Penydarren Park：Penydarren Park is a sports stadium in Merthyr Tydfil, Wales, that is the present home ground of Merthyr Town F.C.. Historically used for varying sports, it has been the home to two professional football teams, Merthyr Town and Merthyr Tydfil F.C.. It has a ca…
- 其余 16 个证据单元见完整 JSON 账本。

## 215 · musique · musique:2hop__20685_712629:answerable

**原题：** Who was the spouse of the British queen?

**原金标：** `[["Philip Mountbatten"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** “The British queen” has no name or reign; Victoria/Albert and Elizabeth II/Philip yield different valid readings.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-7837b74396b30dd963171db9` · Wedding dress of Princess Elizabeth：The wedding dress of Princess Elizabeth was worn by the future Queen Elizabeth II at her wedding to Philip Mountbatten, Duke of Edinburgh on 20 November 1947 in Westminster Abbey. Given the rationing of clothing at the time, she still had to purchase the mater…
- 原支持 `s-09d01687fef7b967f2216f9b` · British Empire：Most former British colonies and protectorates are among the 53 member states of the Commonwealth of Nations, a non-political, voluntary association of equal members, comprising a population of around 2.2 billion people. Sixteen Commonwealth realms voluntarily…

## 217 · musique · musique:2hop__235471_565925:answerable

**原题：** What record label did the performer of The Place and the Time belong to?

**原金标：** `[["Columbia Records"]]`；原标可答。

**问题类型：** `label_relation_ambiguity`。

**裁定：** Moby Grape has releases on multiple labels, including the Sundazed album named in the question and the Columbia compilation used by gold. “Belong to” does not fix the label or date.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-1490821000a72efc88733cba` · The Place and the Time：The Place and the Time is a compilation album of demos, outtakes, alternative versions and live versions of songs by Moby Grape, released by Sundazed Records in 2009 in CD and double LP format.
- 原支持 `s-7dbb8d7c84e19fd45ed695a8` · Great Grape：Great Grape is a compilation album released by Columbia Records in 1972 that compiles songs from three of Moby Grape's Columbia albums - "Moby Grape", "Wow", and "Moby Grape '69". It has been speculated that Columbia's decision to release this album was based …

## 218 · musique · musique:2hop__29191_59955:answerable

**原题：** What country is between Lithuania and Frederic Chopin's home country?

**原金标：** `[["Kaliningrad Oblast"]]`；原标可答。

**问题类型：** `gold_target_type_error`。

**裁定：** Kaliningrad Oblast is a Russian region, not a country. The region lies between Poland and Lithuania; country-level answer would be Russia.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-56b48ce62397420e8397480d` · Paris：In the late 12th-century, a school of polyphony was established at the Notre-Dame. A group of Parisian aristocrats, known as Trouvères, became known for their poetry and songs. Troubadors were also popular. During the reign of Francois I, the lute became popul…
- 原支持 `s-e0115ac692e413920366226f` · Kaliningrad：Kaliningrad (Russian: Калининград, IPA: (kəljɪnjɪnˈɡrat); former German name: Königsberg; Yiddish: קעניגסבערג, Kenigsberg; Russian: Кёнигсберг, tr. Kyonigsberg; Old Prussian: Twangste, Kunnegsgarbs, Knigsberg) is the administrative center of Kaliningrad Oblast…

## 221 · musique · musique:2hop__341305_332933:unanswerable

**原题：** What is the capital of the county that Pine Springs is located in?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `entity_ambiguity`。

**裁定：** Pine Springs has no locality/state qualifier and the chain to a county seat is missing; original local unanswerability is not verified globally.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-15a70397d08abd1d6c1aa768` · MacDonald Airfield：MacDonald Airfield was an airfield built alongside the former northern road, west of Stuart Highway, north of Pine Creek, Northern Territory, Australia during World War II.
- 原上下文 `s-055452b4a380c8f422414e5d` · Federalism：Usually, a federation is formed at two levels: the central government and the regions (states, provinces, territories), and little to nothing is said about second or third level administrative political entities. Brazil is an exception, because the 1988 Consti…
- 原上下文 `s-36ecbc9f819cfff18f2c414f` · Mansehra (Rural)：Mansehra (Rural) is a Union Council (an administrative subdivision) of Mansehra District in Khyber-Pakhtunkhwa province of Pakistan. It is located in the south of the district and to the southeast of the district capital, Mansehra.
- 原上下文 `s-88dea7c087999db45e318b11` · Mount Franklin (Australian Capital Territory)：Mount Franklin is a mountain with an elevation of in the Brindabella Ranges that is located on the border between the Australian Capital Territory and New South Wales, Australia. The summit of the mountain is located in the Australian Capital Territory.
- 其余 16 个证据单元见完整 JSON 账本。

## 223 · musique · musique:2hop__358270_108549:answerable

**原题：** Who wrote the TV Series containing the Finale episodes?

**原金标：** `[["Alfred Gough"], ["Miles Millar"]]`；原标可答。

**问题类型：** `scope_dependent`, `writer_creator_ambiguity`。

**裁定：** “Finale” does not uniquely identify a series; additionally the finale writers and the series developers are different people, while “wrote the TV series” leaves their roles unresolved.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-d2f5e23ad462060f017fbbac` · Finale (Smallville)："Finale" is the title of the two-episode series finale of the superhero television series "Smallville". The episodes are the 21st and 22nd of the 10th season, and the 216th and 217th episodes overall. The finale originally aired on The CW in the United States …
- 原支持 `s-1f6cf48bdb76b00506f3e073` · Smallville：Smallville is an American television series developed by writer-producers Alfred Gough and Miles Millar, based on the DC Comics character Superman created by Jerry Siegel and Joe Shuster. The series, initially broadcast by The WB, premiered on October 16, 2001…

## 226 · musique · musique:2hop__418941_429437:unanswerable

**原题：** What is the mouth of the river which serves as the mouth of the Bumping River?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `missing_reference_chain`。

**裁定：** The river chain is absent from the local reference. Global unanswerability has not been established, so unsupported guesses and abstentions cannot be compared to an empty answer as semantic truth.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-12e8aa82569ecfdfcabad2c2` · Don't Look a Smith Horse in the Mouth："Don't Look a Smith Horse in the Mouth" is the tenth episode of the sixth season of "American Dad!". It aired on January 3, 2010 on Fox and was the first "American Dad!" episode to air in 720p high-definition.
- 原上下文 `s-1ac8ade730cfe60b4ec0f2fe` · Livingston, Guatemala：Livingston is the name of a town in Izabal Department, eastern Guatemala, at the mouth of the Río Dulce at the Gulf of Honduras. The town serves as the municipal seat of the municipality of the same name. It was Guatemala's main port on the Caribbean Sea befor…
- 原上下文 `s-5186cd6b3124cfc4ec126c24` · Ctenophora：The Beroida, also known as Nuda, have no feeding appendages, but their large pharynx, just inside the large mouth and filling most of the saclike body, bears "macrocilia" at the oral end. These fused bundles of several thousand large cilia are able to "bite" o…
- 原上下文 `s-1acf9e1379215347d4563139` · Pieta, California：Pieta is an unincorporated community in Mendocino County, California. It is located near the mouth of Pieta Creek southeast of Hopland, at an elevation of 476 feet (145 m).
- 其余 16 个证据单元见完整 JSON 账本。

## 229 · musique · musique:2hop__525581_161450:answerable

**原题：** Where is the province of Aqqala County located?

**原金标：** `[["in the north-east of the country south of the Caspian Sea"], ["Caspian Sea"]]`；原标可答。

**问题类型：** `answer_granularity_ambiguity`, `gold_incomplete`。

**裁定：** Iran correctly locates Golestan Province at country granularity; gold expects a relative subnational location and also accepts bare Caspian Sea, which is not where the province is literally located.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-53ee573f8a1c36faf231fcd3` · Aqqala County：Aqqala County () (Persian Sepid Dez), is a county in Golestan Province in Iran. The capital of the county is Aqqala. At the 2006 census, the county's population was 109,440, in 22,804 families. The county consists of two districts: Voshmgir District and Centra…
- 原支持 `s-76a1fae9b36729acb7b772ef` · Golestan Province：Golestān Province (Persian: استان گلستان‎, Ostān-e Golestān) is one of the 31 provinces of Iran, located in the north-east of the country south of the Caspian Sea. Its capital is Gorgan.

## 234 · musique · musique:2hop__64372_83289:unanswerable

**原题：** Who is the guy in the One Last Time music video by the singer of a new version of Somewhere Over the Rainbow?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `missing_reference_chain`。

**裁定：** The original reference omits a needed bridge from the singer to the specific music video/person; globally retrieved evidence may make the item answerable. Empty original gold is not semantic truth.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-6c013860914a1b45bb6c2f1f` · Since You Been Gone：Rainbow's cover version, released in 1979 with Graham Bonnet on lead vocals, was a Top 10 single in the United Kingdom and was named the 82nd best hard rock song of all time by VH1. Rainbow's version was used in a 2009 T - Mobile advertisement filmed at Liverp…
- 原上下文 `s-39caedec42f78de3a0141a86` · Christmas Is the Time to Say 'I Love You'：``Christmas Is the Time to Say 'I Love You' ''is a holiday rock song by Billy Squier, released in 1981 as the B - side of his hit`` My Kinda Lover'' (Capitol 5037). In 1981, a video of the song was recorded with MTV VJs and staff members singing along with a l…
- 原上下文 `s-42ca060cc579fd8cab3509d7` · What Makes You Beautiful：The music video for ``What Makes You Beautiful ''was directed by John Urbano. Filmed over two days in July 2011, it premiered on 19 August 2011. On each of the five days preceding the video's premiere, One Direction posted a teaser trailer of the video online.…
- 原上下文 `s-f705af7dd5861a59c777f8ab` · Simon Lamunière：His experience as curator began at the Centre pour l'image contemporaine (CIC) with the International Video Week program, one of the first video festivals in Switzerland and Europe. He then created and managed "Version 1.0", the first edition of what became th…
- 其余 16 个证据单元见完整 JSON 账本。

## 235 · musique · musique:2hop__647996_551681:answerable

**原题：** What is another notable work made by the author of Miss Sara Sampson?

**原金标：** `[["Emilia Galotti"]]`；原标可答。

**问题类型：** `nonunique_reference`。

**裁定：** Lessing has many other notable works, including Nathan the Wise, Minna von Barnhelm and Laocoon; the single gold Emilia Galotti is not exhaustive.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-36fb64f245be5ea5c5be77cf` · Miss Sara Sampson：Miss Sara Sampson (original spelling "Miß Sara Sampson") is a play by the Enlightenment philosopher, Gotthold Ephraim Lessing. Written in 1755 while the author was living in Potsdam, it is seen by many scholars to be one of the first bourgeois tragedies. In th…
- 原支持 `s-05805152a04aff0db5999441` · Emilia Galotti：Emilia Galotti is a play in five acts by Gotthold Ephraim Lessing (1729–1781), which premiered on 8 March 1772 in Brunswick ("Braunschweig" in German). The work is a classic example of German "bürgerliches Trauerspiel" (bourgeois tragedy). Other works in this …

## 239 · musique · musique:2hop__780652_93362:answerable

**原题：** What film features Anil Devgan's sibling?

**原金标：** `[["Yeh Raaste Hain Pyaar Ke"]]`；原标可答。

**问题类型：** `nonunique_reference`。

**裁定：** The sibling Ajay Devgan appears in many films, including Raju Chacha and Blackmail explicitly named in the reference. Gold restricts to one arbitrary film.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-d6d87e4463c48064d3876378` · Anil Devgan：Anil Devgan is the son of Veeru Devgan, brother of actor Ajay Devgan, and brother-in-law of actress Kajol. Anil Devgan has also tried his hand with directing, and has directed his brother in two of his films: Raju Chacha and Blackmail (2005 film).
- 原支持 `s-30d32f5c703bc981f3b41eaa` · Yeh Raaste Hain Pyaar Ke：Yeh Raaste Hain Pyaar Ke Directed by Deepak Shivdasani Produced by Deepak Shivdasani Pradeep Sadarangani Written by Kader Khan, Robin Bhatt, K.V. Shankar (dialogues) Screenplay by Robin Bhatt, Akash Khurana, Umanand Singh Story by Deepak Shivadasani Starring M…

## 243 · musique · musique:2hop__815205_590407:unanswerable

**原题：** Where is the headquarters of the organization that the Office of Science Education is a part of?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `missing_reference_chain`。

**裁定：** The office-to-NIH bridge exists in original contexts but NIH headquarters is omitted. Bethesda may be a factual answer, yet the empty original gold cannot establish whether it is supported by this pooled corpus.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-e2878199554fc9aad6196a8b` · HOSA (organization)：HOSA - Future Health Professionals, formerly known as Health Occupations Students of America (HOSA), is a national career and technical student organization endorsed by the U.S. Department of Education and the Health Science Technology Education Division of AC…
- 原上下文 `s-f9a09a454256a63c3e4806e0` · Roshd Biological Education：Roshd Biological Education is a quarterly science educational magazine covering recent developments in biology and biology education for a biology teacher Persian -speaking audience. Founded in 1985, it is published by The Teaching Aids Publication Bureau, Org…
- 原上下文 `s-b3f590bc2791beafddd91887` · Subway (restaurant)：Advertising affiliates include Subway Franchisee Advertising Fund Trust, Ltd.; Subway Franchisee Advertising Fund Trust, B.V.; Subway Franchisee Canadian Advertising Trust; etc.Subway's international headquarters are in Milford, Connecticut, with five regional…
- 原上下文 `s-29314718149a912c1de7b254` · JAXA：On 1 October 2003, three organizations were merged to form the new JAXA: Japan's Institute of Space and Astronautical Science (ISAS), the National Aerospace Laboratory of Japan (NAL), and National Space Development Agency of Japan (NASDA). JAXA was formed as a…
- 其余 16 个证据单元见完整 JSON 账本。

## 245 · musique · musique:2hop__837900_71701:answerable

**原题：** What date did the Japanese get to the city where New Plaza Stadium is located, and the rest of the Guangdong province?

**原金标：** `[["November 5"]]`；原标可答。

**问题类型：** `gold_incomplete`, `temporal_multitarget`。

**裁定：** Foshan was taken by November 5, but the entire province only by the end of November. Gold contains only the city date despite the question requesting both.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-f9318e822a4e1080c80f5905` · New Plaza Stadium：New Plaza Stadium (Simplified Chinese: 佛山新广场体育场) was a multi-use stadium in Foshan, China. It was used mostly for football matches and was one of the six stadiums used for the 1991 FIFA Women's World Cup. The stadium was a capacity of 14,000 people.
- 原支持 `s-309ce00cc8bbf47b4bdd564a` · Twenty-First Army (Japan)：On October 12, the 18th and 104th Divisions landed, followed by command units the following day. By October 21, the provincial capital of Guangzhou was under Japanese control. The IJA 5th Division continued to advance up the Pearl River and by November 5 had t…

## 246 · musique · musique:2hop__848156_130869:answerable

**原题：** What state contains the place where Fatata te Miti is displayed?

**原金标：** `[["Washington, D.C."], ["Washington"], ["DC"], ["Washington, DC"], ["D.C."]]`；原标可答。

**问题类型：** `geographic_type_error`。

**裁定：** Washington, DC / District of Columbia is a federal district, not a state; outputs name the correct gallery location but the requested type is wrong.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-f5f341fbe4bd64a7e207cb5f` · National Gallery of Art：The National Gallery of Art, and its attached Sculpture Garden, is a national art museum in Washington, D.C., located on the National Mall, between 3rd and 9th Streets, at Constitution Avenue NW. Open to the public and free of charge, the museum was privately …
- 原支持 `s-76c9daf90d79091c8933814b` · Fatata te Miti (By the Sea)：Fatata te Miti is an 1892 oil painting by French artist Paul Gauguin, located in the National Gallery of Art, in Washington, DC.

## 249 · musique · musique:3hop1__103180_214799_259594:unanswerable

**原题：** What is the country of the border troops of the country of the literature of the country Wilfried Grobner was in?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `question_corruption`。

**裁定：** Nested country/literature/border-troops wording does not establish a usable chain; empty original gold is not independently verified global unanswerability.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-37dd2472ce549358607c9533` · Warsaw Pact：In 1956, following the declaration of the Imre Nagy government of withdrawal of Hungary from the Warsaw Pact, Soviet troops entered the country and removed the government. Soviet forces crushed the nationwide revolt, leading to the death of an estimated 2,500 …
- 原上下文 `s-0b44ebe2c5211ca518cb694a` · Soul of Country Music：Soul of Country Music is the ninth studio album by American country artist, Connie Smith. The album was released in December 1967 on RCA Victor Records and was produced by Bob Ferguson. With no singles spawned from the album, "Soul of Country Music" contained …
- 原上下文 `s-4c710e895d5668cf47320335` · International Booker Prize：Year Name Work Translator Country Language (s) Literary tradition 2005 Ismail Kadare Albania Albanian Albanian literature 2007 Chinua Achebe n / a Nigeria English Nigerian literature 2009 Alice Munro n / a Canada English Canadian literature 2011 Philip Roth n …
- 原上下文 `s-d27dc1146c7761ccd44ac46b` · 2018 Ouagadougou attacks：In the aftermath of the 2011 Libyan Civil War, militant attacks have increased due to a large influx of weapons and fighters into the region. Neighbouring Mali faced conflict in Azawad that threatened to split the country. Since 2015, Burkina Faso has faced cr…
- 其余 16 个证据单元见完整 JSON 账本。

## 250 · musique · musique:3hop1__104237_214799_259594:answerable

**原题：** What is the abbreviated form of the name of the country where Alfred Kurella hails from?

**原金标：** `[["GDR"], ["German Democratic Republic"], ["East Germany"]]`；原标可答。

**问题类型：** `historical_country_ambiguity`。

**裁定：** Source calls Kurella German and a functionary in East Germany; “hails from” does not distinguish country of origin from later political affiliation, nor name the historical period.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-42a6312eb8bc2324606aa6f4` · Literature of East Germany：East German literature is the literature produced in East Germany from the time of the Soviet occupation in 1945 until the end of the communist government in 1990. The literature of this period was heavily influenced by the concepts of socialist realism and co…
- 原支持 `s-f04de86e501cc2a218b5dab9` · Heinz-Josef Große：Heinz-Josef Große was a 34-year-old East German (GDR) construction worker who was shot and killed on 29 March 1982 by GDR border guards on the Inner German border at Schifflersgrund, near Bad Sooden-Allendorf.
- 原支持 `s-9c38520051ee1bc96826c21c` · Alfred Kurella：Alfred Kurella (May 2, 1895 – June 12, 1975) was a German author and functionary of the Socialist Unity Party of Germany (SED) in East Germany.

## 251 · musique · musique:3hop1__127821_157828_239539:answerable

**原题：** Where is the headquarters of Radio Television of this country that has a co-official language that is the same as the language of Olivera Markovic?

**原金标：** `[["Pristina"]]`；原标可答。

**问题类型：** `entity_reference_ambiguity`。

**裁定：** “This country” is unnamed; a country with Serbian as co-official language is not uniquely identified by the actor’s language alone.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-c5354f5171bc420992eacc14` · Olivera Marković：Olivera Marković (née Đorđević; ; 3 May 1925 – 2 July 2011) was a Serbian actress. She appeared in 170 films and television shows between 1946 and 2005. She won the Golden Arena for Best Actress in 1964 for her role in "Službeni položaj".
- 原支持 `s-4f82b9b107b1408a647fbfbb` · Media in Pristina：Media in Pristina have followed all elections held in Kosova, especially a great impact was noted in Kosova local elections, 2013,where media dedicated most of their time in political debates,advertisements and political parties programs.
- 原支持 `s-0e51de0c8ece07cb66a870c8` · Serbian language：Serbian (српски / srpski, pronounced [sr̩̂pskiː]) is the standardized variety of the Serbo-Croatian language mainly used by Serbs. It is the official language of Serbia, co-official in the territory of Kosovo, and one of the three official languages of Bosnia …

## 252 · musique · musique:3hop1__128554_39743_24526:answerable

**原题：** What is the average winter daytime temperature in the region where Richmond is found, in the state where WIRR operates?

**原金标：** `[["upper 40s–lower 50s °F"]]`；原标可答。

**问题类型：** `gold_entity_conflation`。

**裁定：** WIRR serves Virginia, Minnesota; Richmond is in Virginia state; temperature text concerns North Carolina. The gold crosses three different geographic entities.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-78282d3e424edecd4a0ffae7` · WIRR：WIRR (90.9 FM) is a radio station licensed to Virginia, Minnesota, serving the Iron Range area. The station is owned by Minnesota Public Radio (MPR), and airs MPR's "Classical Music Network", originating from KSJN in Minneapolis/St. Paul.
- 原支持 `s-fc61f283e91be6ddd65e6077` · North Carolina：In winter, the Piedmont is colder than the coast, with temperatures usually averaging in the upper 40s–lower 50s °F (8–12 °C) during the day and often dropping below the freezing point at night. The region averages around 3–5 in (8–13 cm) of snowfall annually …
- 原支持 `s-e87b199db3b2bd90c61457d3` · Richmond, Virginia：Richmond is located at 37°32′N 77°28′W﻿ / ﻿37.533°N 77.467°W﻿ / 37.533; -77.467 (37.538, −77.462). According to the United States Census Bureau, the city has a total area of 62 square miles (160 km2), of which 60 square miles (160 km2) is land and 2.7 square m…

## 255 · musique · musique:3hop1__136129_87694_64412:unanswerable

**原题：** When did the place where the head of the catholic region named after the same saint as the Dakovo Cathedral become its own country?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `question_corruption`。

**裁定：** “Head of the Catholic region named after the same saint” is ambiguous between a diocese and the Catholic religion; country establishment is not provided in original reference.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-024e0c49b0369d173ba67e73` · Ferns Cathedral：The Cathedral Church of St Edan is a cathedral of the Church of Ireland in Ferns, County Wexford in Ireland. It is in the ecclesiastical province of Dublin. Until 1949, the designation of the Cathedral was the Cathedral Church of St. Ædan, a variant spelling o…
- 原上下文 `s-fd043897169651561280fd92` · Rodez Cathedral：Rodez Cathedral () is a Roman Catholic church located in the town of Rodez, France. The cathedral is a national monument and is the seat of the Bishopric of Rodez. The closed west front once formed part of the city wall of Rodez.
- 原上下文 `s-f57a7881a86729fdc7493994` · Pazim：Pazim is the second tallest building in Szczecin, Poland, after the St. James cathedral, if including Pazim's spire. It contains twenty-three floors and is 92 meters tall. The building was designed by Milijenko Dumencić. It is the headquarters of PZM, hence th…
- 原上下文 `s-0f67166e02e120def7f31bee` · Plymouth：Plymouth has about 150 churches and its Roman Catholic cathedral (1858) is in Stonehouse. The city's oldest church is St Andrew's (Anglican) located at the top of Royal Parade—it is the largest parish church in Devon and has been a site of gathering since AD 8…
- 其余 16 个证据单元见完整 JSON 账本。

## 257 · musique · musique:3hop1__145427_106426_157788:answerable

**原题：** What is one railway line in the country the produced The Longest Night in the city where You Wenhui was born?

**原金标：** `[["Yamanote Line loop"], ["Yamanote Line"]]`；原标可答。

**问题类型：** `gold_entity_conflation`。

**裁定：** The film is a China–Japan collaboration and You Wenhui was born in Shanghai; the gold follows unrelated Tokyo/Yamanote Line instead of the requested birthplace.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-0f790242d6fe06ccddd60900` · The Longest Night in Shanghai：The Longest Night in Shanghai () is a 2007 film produced by Japan's Movie Eye Entertainment and directed by Chinese director Zhang Yibai. It is a rare collaboration between China and Japan.
- 原支持 `s-feb52b4c2a9dfa03739a75d7` · Tokyo：Rail is the primary mode of transportation in Tokyo, which has the most extensive urban railway network in the world and an equally extensive network of surface lines. JR East operates Tokyo's largest railway network, including the Yamanote Line loop that circ…
- 原支持 `s-1c5cced86633a17dec66f49c` · You Wenhui：You Wenhui (; born October 20, 1979 in Shanghai) is a female Chinese beach volleyball player who competed in the 2004 Summer Olympics.

## 259 · musique · musique:3hop1__241001_568433_47686:answerable

**原题：** Where do Greyhound buses leave from in the city where Arna Selznick's employer is headquartered?

**原金标：** `[["Toronto Coach Terminal"]]`；原标可答。

**问题类型：** `missing_reference_bridge`。

**裁定：** The supporting Arna Selznick and unrelated Natalie Turner paragraphs do not establish the employer-headquarters city; the Toronto terminal itself is correctly identified but the gold reasoning chain is incomplete.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-ffcfa943e15ab32fe7d1a0f4` · Arna Selznick：Arna Selznick is a Canadian director and artist, known for directing Nelvana's 1985 animated film "The Care Bears Movie". Arna owns and operates a studio called Dancingmonkeys with her husband/partner John van Bruggen.
- 原支持 `s-e8b40c84fe43b3e8442655b0` · Natalie Turner：Turner is currently a professor for the Classical Animation program at Sheridan College in Oakville. Previously she taught for Max the Mutt Animation School in Toronto. She will teach the fifth year of the Animex (Animation Exploration) workshop at Visual Arts…
- 原支持 `s-51bc613de0212368d66528a9` · Toronto Coach Terminal：The Toronto Coach Terminal is the central bus station for inter-city services in Toronto, Ontario, Canada. It is located at 610 Bay Street, in the city's Downtown. The terminal is owned by Toronto Coach Terminal Inc., a wholly owned subsidiary of the Toronto T…

## 260 · musique · musique:3hop1__254856_400692_51423:answerable

**原题：** What is the name of the castle found in the birthplace of the performer of Falling Out?

**原金标：** `[["Casa Loma"]]`；原标可答。

**问题类型：** `missing_reference_bridge`。

**裁定：** Source links Sekou Lumumba to Toronto by residence and Serena Ryder by performance, not Serena Ryder’s birthplace. Gold uses a bridge the reference does not establish.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-194809b01fa361f86ab1bfa5` · Sekou Lumumba：Sekou Lumumba is a Canadian musician, based in Toronto, who has been drummer for such artists and groups as The Illegal Jazz Poets, Thornley, Edwin & the Pressure, Goodbye Glory, Ivana Santilli, Kardinal Offishall, Serena Ryder, 24-7 Spyz and Bedouin Soundclas…
- 原支持 `s-924068539db6f5ed3f4d8506` · Falling Out (Serena Ryder album)：Falling Out is the debut studio album by Millbrook, Ontario singer Serena Ryder, released in Peterborough, Ontario, Canada in December 1999 by the independent record label Mime Radio.
- 原支持 `s-d0eae2f82aecf6fbfc913eb3` · Casa Loma：Casa Loma (Spanish for ``Hill House '') is a Gothic Revival style mansion and garden in midtown Toronto, Ontario, Canada, that is now a historic house museum and landmark. It was constructed from 1911 to 1914 as a residence for financier Sir Henry Pellatt. The…

## 261 · musique · musique:3hop1__28841_547811_41132:answerable

**原题：** How many times did the plague occur in the birth place of Concerto in C Major Op 3 6's composer?

**原金标：** `[["22"]]`；原标可答。

**问题类型：** `counting_scope_error`。

**裁定：** Gold 22 counts only Venice outbreaks in 1361–1528, while the same source lists later outbreaks. Question asks an unbounded count and does not name that interval.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-f05e29c62566ab245b59c447` · Orlando furioso (Vivaldi, 1714)：Orlando furioso RV 819 (, Teatro San Angelo, Venice 1714) is a three-act opera surviving in manuscript in Antonio Vivaldi's personal library, only partly related to his better known Orlando furioso (RV 728) of 1727. It is a recomposition of an "Orlando furioso…
- 原支持 `s-d03ab9b022987bee8f1606b0` · Black Death：In 1466, perhaps 40,000 people died of the plague in Paris. During the 16th and 17th centuries, the plague was present in Paris around 30 per cent of the time. The Black Death ravaged Europe for three years before it continued on into Russia, where the disease…
- 原支持 `s-1eba5e9c49814d2f40d8fe6d` · Mandolin：Antonio Vivaldi composed a mandolin concerto (Concerto in C major Op.3 6) and two concertos for two mandolins and orchestra. Wolfgang Amadeus Mozart placed it in his 1787 work Don Giovanni and Beethoven created four variations of it. Antonio Maria Bononcini co…

## 263 · musique · musique:3hop1__302383_503371_21711:unanswerable

**原题：** When was the Palau de la Generalitat constructed in the city where Martin from the region where Codo is located died?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `missing_reference_chain`。

**裁定：** Original reference does not complete the Codo–Martin–city–palace construction chain; global unanswerability has not been established.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-37aec79c8322033d56ce21d5` · Alajeró：Alajeró is a municipality on the island of La Gomera in the province of Santa Cruz de Tenerife in the Canary Islands. It is located on the south coast, SW of the capital San Sebastián de la Gomera. As well as being a municipality, Alajeró is also the name of t…
- 原上下文 `s-466437cd67abb424d05b37ca` · Catalan language：According to the Statistical Institute of Catalonia in 2008 the Catalan language is the second most commonly used in Catalonia, after Spanish, as a native or self-defining language. The Generalitat of Catalunya spends part of its annual budget on the promotion…
- 原上下文 `s-292e99a0fab6795663696184` · Santa Cruz de las Flores, Tlajomulco de Zúñiga, Jalisco：Santa Cruz de las Flores is the name of a town located south of Tlajomulco de Zuñiga, in the state of Jalisco, Mexico. It has been called Xochitlan, meaning "Place of Flowers" ("xōchitl" is Nahuatl for flower ).
- 原上下文 `s-d66078f3635eb4de86bb0e38` · Palau Tiger Team：Palau Tiger Team was a Palauan association football club which competed in the Palau Soccer League, the top level league in Palau, in 2006-07, when they finished fourth losing 2-4 to Mount Everest Nepal in the third place play-off. Due to fragmentary records, …
- 其余 16 个证据单元见完整 JSON 账本。

## 265 · musique · musique:3hop1__353661_629431_64412:unanswerable

**原题：** When did the city where the author of Sacerdotii Nostri Primordia died become its own country?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `missing_reference_chain`。

**裁定：** Original reference omits the encyclical author’s place of death and its sovereignty date; an empty local gold cannot judge 1929 or abstention against full-corpus evidence.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-b71873c3e811bc8e1b44c8ef` · 2013 World Men's Handball Championship：The 2013 World Men's Handball Championship was the 23rd World Men's Handball Championship, an international handball tournament that took place in Spain from 11 to 27 January 2013. This was the first time Spain hosted the World Men's Handball Championship, bec…
- 原上下文 `s-4c3594df611e025d65a181f9` · Hyon Chol-hae：During the Korean War, Hyon served as Kim Il-sung's bodyguard, a role which gave him "a place in North Korea's revolutionary history". He was director of the General Logistics Department of the Korean People's Army from 1986 to 1995, when he was appointed depu…
- 原上下文 `s-41a2ada441b5f739138bd2fb` · Death Note (2017 film)：Nat Wolff as Light Turner / ``Kira '': A high - school student who discovers the titular`` Death Note'' and uses it to kill criminals by writing their names and causes of death, and thus, alongside Mia Sutton, becoming the world - famous serial killer known as…
- 原上下文 `s-b54a806b3aa2666e0ad35210` · Distant Drums (song)："Distant Drums" is a song which provided US singer Jim Reeves with his only UK No. 1 hit – albeit posthumously – in the United Kingdom in 1966, some two years after his death in a plane crash on 31 July 1964. The song remained in the UK Singles Chart for 45 we…
- 其余 16 个证据单元见完整 JSON 账本。

## 269 · musique · musique:3hop1__491648_339990_54675:answerable

**原题：** What mountains can you see from Portland, in the state containing the county where Cole's creek is located?

**原金标：** `[["Tualatin Mountains"]]`；原标可答。

**问题类型：** `gold_entity_conflation`。

**裁定：** Coles Creek is in Columbia County, Pennsylvania; gold bridges via a different Columbia County in Oregon to Portland, Oregon.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-e7976a9ee84fc780462e0418` · Socialist Party of Oregon (Columbia County, Oregon)：The Socialist Party of Oregon in Columbia County, Oregon began around the First Red Scare. The first year (1914) it went mainstream, the Socialist party had 27 more registered members than the Prohibition Party, who were some members of the Suffrage movement. …
- 原支持 `s-5889a8d8efd65a8e2f0735be` · Coles Creek (Pennsylvania)：Coles Creek (also known as Cole's Creek) is a tributary of Fishing Creek, in Columbia County, Pennsylvania and Sullivan County, Pennsylvania, in the United States. It is long and is the first named tributary of Fishing Creek downstream of where East Branch Fis…
- 原支持 `s-51d1fa10d2504e539891eb7f` · Portland, Oregon：Though much of downtown Portland is relatively flat, the foothills of the Tualatin Mountains, more commonly referred to locally as the ``West Hills '', pierce through the northwest and southwest reaches of the city. Council Crest Park, commonly thought of as t…

## 270 · musique · musique:3hop1__540585_568433_51423:unanswerable

**原题：** What is the name of the castle in the city where the headquarters of the production company of Miss Spider's Sunny Patch Kids is located?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `missing_reference_chain`。

**裁定：** The production company/headquarters/castle chain is incomplete in original reference; no verified full-corpus negative.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-0f9d65e4d747f6a9f8f54151` · Cabbage Patch Kids：The name change to Cabbage Patch Kids was made in 1982 when Xavier's company, Original Appalachian Artworks, began to license a smaller version of the handmade creations to a toy manufacturer named Coleco. An abbreviated version of the discovery legend was rep…
- 原上下文 `s-b18c090892332c3a86fd5bd7` · P and W Patch, Pennsylvania：P and W Patch is an unincorporated community in Independence Township, Washington County, Pennsylvania, United States. P and W Patch is located along Pennsylvania Route 50 west-northwest of West Middletown.
- 原上下文 `s-14db6192daa1e886da259808` · The Home Depot：The Home Depot, Inc. or Home Depot is an American home improvement supplies retailing company that sells tools, construction products, and services. The company is headquartered at the Atlanta Store Support Center in unincorporated Cobb County, Georgia (with a…
- 原上下文 `s-93f3c75f7000c992889cf10d` · Cabbage Patch Kids：Cabbage Patch Kids Type Dolls Company Coleco (1982 -- 1988) Hasbro (1988 -- 1994) Mattel (1994 -- 2003) Toys ``R ''Us (2003) Play Along (2004 -- 2011) Jakks Pacific (2011 -- 2014) Wicked Cool Toys (2015 -- present) Country United States Availability 1978 -- pr…
- 其余 16 个证据单元见完整 JSON 账本。

## 271 · musique · musique:3hop1__604991_339990_15538:unanswerable

**原题：** When did the state that Frozen Run is located in reinstate the death penalty?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `missing_reference_chain`。

**裁定：** The river-to-state-to-death-penalty reinstatement chain is absent in original reference; a full-corpus negative is unverified.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-224df321e8242695c90826c0` · Kiri Territory：Kiri Territory is an administrative region in the Mai-Ndombe District of Bandundu Province, Democratic Republic of the Congo. The headquarters is the town of Kiri.
- 原上下文 `s-b5735cb1385cad3f010088fc` · List of territorial entities where English is an official language：The following is a list of territories where English is an official language, that is, a language used in citizen interactions with government officials. In 2015, there were 54 sovereign states and 27 non-sovereign entities where English was an official langua…
- 原上下文 `s-a720ac0296f1612c6f09fb93` · Frozen Run：Frozen Run is a tributary of Hemlock Creek in Columbia County, Pennsylvania, in the United States. It is approximately long and flows through Hemlock Township. The watershed of the stream has an area of . The stream flows through Frosty Valley and is also near…
- 原上下文 `s-055452b4a380c8f422414e5d` · Federalism：Usually, a federation is formed at two levels: the central government and the regions (states, provinces, territories), and little to nothing is said about second or third level administrative political entities. Brazil is an exception, because the 1988 Consti…
- 其余 16 个证据单元见完整 JSON 账本。

## 272 · musique · musique:3hop1__617062_127905_89859:answerable

**原题：** When does the sport which has Jim Wilson's Cleveland team as a member start the regular season?

**原金标：** `[["March 29, 2018"]]`；原标可答。

**问题类型：** `temporal_scope_ambiguity`。

**裁定：** Question asks the sport’s regular-season start without naming a year; late March/early April is a correct general answer, while gold chooses 2018 specifically.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-a1c54d49fc6b0eb8fc1575f8` · Jim Wilson (first baseman)：He was released by the Indians following the 1986 season. After a brief tour in the Minnesota Twins organization, Wilson signed as a free agent with the Seattle Mariners on March 1, 1988, playing five games for them in the 1989 season.
- 原支持 `s-35d9c39feb36710471559f6f` · 2018 Major League Baseball season：The 2018 Major League Baseball season began on March 29, 2018, and is scheduled to end on September 30. The Postseason will begin on October 2. The 2018 World Series is set to begin on October 23, and a potential Game 7 is scheduled on October 31.
- 原支持 `s-36d245b298d480425c027964` · Cleveland Indians：The Cleveland Indians are an American professional baseball team based in Cleveland, Ohio. The Indians compete in Major League Baseball (MLB) as a member club of the American League (AL) Central division. Since , they have played at Progressive Field. The team…

## 281 · musique · musique:3hop1__90327_78276_68042:answerable

**原题：** When did the majority party in the body which approves members of the American Cabinet gain control of the House?

**原金标：** `[["the 2010 election"]]`；原标可答。

**问题类型：** `temporal_scope_ambiguity`。

**裁定：** Senate majority and “gain control of the House” depend on the reference date and which transition is meant; the question provides neither. Gold selects the snapshot’s 2010 transition.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-423351c9ec65141d09ee9204` · 2010 United States House of Representatives elections：Republicans regained control of the chamber they had lost in the 2006 midterm elections, picking up a net total of 63 seats and erasing the gains Democrats made in 2006 and 2008. Although the sitting U.S. President's party usually loses seats in a midterm elec…
- 原支持 `s-3f867c65e8d0360341923193` · Cabinet of the United States：The heads of the executive departments and all other federal agency heads are nominated by the President and then presented to the Senate for confirmation or rejection by a simple majority (although before the use of the ``nuclear option ''during the 113th US …
- 原支持 `s-9f63527650677b248d917adf` · Party leaders of the United States Senate：The Senate is currently composed of 51 Republicans, 47 Democrats, and 2 independents, both of whom caucus with the Democrats.

## 282 · musique · musique:3hop2__28476_14777_9331:answerable

**原题：** What dissolved the privileges in Poland of the empire that controlled Estonia during The Great Awakening, the Kingdom that acquired some Thuringian territory in 1815, and the Habsburg Monarchy?

**原金标：** `[["March Constitution of Poland"], ["PL"], ["POL"], ["Poland"]]`；原标可答。

**问题类型：** `relation_premise_error`, `gold_incomplete`。

**裁定：** Source concerns privileges of Polish nobility under three empires, not the empires’ own privileges. Gold also accepts bare Poland, which does not identify the dissolving constitution.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-a777fb19113ac9c213f936aa` · Szlachta：Poland's nobility were also more numerous than those of all other European countries, constituting some 10–12% of the total population of historic Polish–Lithuanian Commonwealth also some 10–12% among ethnic Poles on ethnic Polish lands (part of Commonwealth),…
- 原支持 `s-a2d0599024782a3d80234af9` · Franco-Prussian War：The Franco-Prussian War or Franco-German War (German: Deutsch-Französischer Krieg, lit. German-French War, French: Guerre franco-allemande, lit. Franco-German War), often referred to in France as the War of 1870 (19 July 1870 – 10 May 1871), was a conflict bet…
- 原支持 `s-95eaa14141abb9efbe6d09cc` · Estonia：After centuries of Danish, Swedish and German rule the native Estonians started to yearn for independence during the period of national awakening while being governed by the Russian Empire. Established on 24 February 1918, the Republic of Estonia came into exi…

## 284 · musique · musique:3hop2__304722_667199_63959:unanswerable

**原题：** When was the last time the team that Malcolm Briggs was a member of beat the 1894-95 FA cup winner?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `temporal_scope_ambiguity`。

**裁定：** The latest win requires a dated complete match history, not supplied in the reference; original empty gold is not a full-corpus negative.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-9bf36b1546607ccc87c4f25e` · Jonathan Maher：Maher began his inter-county career after emigrating from Ireland when he linked up with the London senior hurling team. As an inter-county hurler he has won one Christy Ring Cup winners' medal and one Nicky Rackard Cup winners' medal.
- 原上下文 `s-9df164c52e90c6401c9ae8d4` · 1990 FIFA World Cup：The tournament was won by West Germany, their third World Cup title. They beat Argentina 1 -- 0 at the Stadio Olimpico in Rome, a rematch of the previous final four years earlier. Italy finished third and England fourth, after both lost their semi-finals in pe…
- 原上下文 `s-14a26edb03c94d4cbcf778bc` · 2017–18 Arsenal F.C. season：Arsenal 2017 -- 18 season Chairman Sir Chips Keswick Manager Arsène Wenger Stadium Emirates Stadium Premier League 6th Community Shield Winners FA Cup Third round EFL Cup Runners - up UEFA Europa League Semi-finals Top goalscorer League: Alexandre Lacazette (1…
- 原上下文 `s-a3dfe5b59bfebf6b11e82cdf` · FA Cup：Many[who?] expected BSkyB to make a bid to show some of the remaining FA Cup games for the remainder of the 2009–10 season which would include a semi-final and shared rights to the final. ESPN took over the package Setanta held for the FA Cup from the 2010–11 …
- 其余 16 个证据单元见完整 JSON 账本。

## 286 · musique · musique:3hop2__90098_19639_10557:unanswerable

**原题：** What was the form of the language that the last name Sylvester comes from, used in the era of the king who united the 9th century tribes, later known as?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `missing_reference_chain`。

**裁定：** Unnamed ninth-century king and missing language-era chain prevent a unique target; original local unanswerability is not a verified full-corpus negative.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-743cb36adebfcb36cadf3172` · Sylvester：Sylvester is a name derived from the Latin adjective silvestris meaning ``wooded ''or`` wild'', which derives from the noun silva meaning ``woodland ''. Classical Latin spells this with i. In Classical Latin y represented a separate sound distinct from i, not …
- 原上下文 `s-52f5ccc2710d2ad3a2184465` · Crimthann mac Fidaig：Crimthann Mór, son of Fidach , also written Crimthand Mór, was a semi-mythological king of Munster and High King of Ireland of the 4th century. He gained territory in Britain and Gaul, but died poisoned by his sister Mongfind. It is possible that he was also r…
- 原上下文 `s-d8bd1aff535b45a0145bd439` · Pitt & Kantrop：Pitt & Kantrop is a children's television programme produced by the Eurovision Network and was shown on BBC One and CBBC in the United Kingdom. Also shown in the Clan TVE channel in Spain. It follows the journey of a 13-year-old boy named Pitt and his pet pter…
- 原上下文 `s-19316aa5b1b81caf5f2efd74` · Morgan Crofton：Morgan Crofton (1826, Dublin, Ireland – 1915, Brighton, England) was an Irish mathematician who contributed to the field of geometric probability theory. He also worked with James Joseph Sylvester and contributed an article on probability to the 9th edition of…
- 其余 16 个证据单元见完整 JSON 账本。

## 289 · musique · musique:4hop1__105401_17130_70784_79935:answerable

**原题：** When was the region that lies to the north of the region where Strangers No More took place and the Persian Gulf created?

**原金标：** `[["1930"]]`；原标可答。

**问题类型：** `spatial_premise_error`。

**裁定：** The wording places the target north of the Israel/Middle East region and Persian Gulf, but gold follows Saudi Arabia from a source saying north of Yemen. The direction/region chain is inconsistent.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-e7b232d53b467c0a9466b03f` · Strangers No More：Strangers No More is a 2010 short documentary film about a school in Tel Aviv, Israel, where children from 48 different countries and diverse backgrounds come together to learn. The parents of these children are among over 300,000 transnational migrant workers…
- 原支持 `s-475246b0bbba924fe734cc82` · Israel：Israel (/ˈɪzreɪəl/ or /ˈɪzriːəl/; Hebrew: יִשְׂרָאֵל‎ Yisrā'el; Arabic: إِسْرَائِيل‎ Isrāʼīl), officially the State of Israel (Hebrew: מְדִינַת יִשְׂרָאֵל‎ Medīnat Yisrā'el [mediˈnat jisʁaˈʔel] ( listen); Arabic: دولة إِسْرَائِيل‎ Dawlat Isrāʼīl [dawlat ʔisraː…
- 原支持 `s-0a483c60733fc9b956a7c2fa` · History of Saudi Arabia：For much of the region's history a patchwork of tribal rulers controlled most of the area. The Al Saud (the Saudi royal family) emerged as minor tribal rulers in Najd in central Arabia. From the mid-18th century, imbued with the religious zeal of the Wahhabi I…
- 原支持 `s-c3121a56b6a0160e088249e4` · Geography of Saudi Arabia：The Kingdom of Saudi Arabia is a country situated in Southwest Asia, the largest country of Arabia, bordering the Persian Gulf and the Red Sea, north of Yemen. Its extensive coastlines on the Persian Gulf and Red Sea provide great leverage on shipping (especia…

## 290 · musique · musique:4hop1__131113_59747_211319_557671:answerable

**原题：** Which county shares a border with the county where the most populous city in the state where Washington State Prison can be found is located?

**原金标：** `[["Presque Isle County"]]`；原标可答。

**问题类型：** `gold_entity_conflation`。

**裁定：** Prison and most populous city refer to Georgia/Atlanta, but the gold jumps through Atlanta, Michigan/Montmorency to Presque Isle County.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-3e03ea3ed0686f7c448819ff` · Washington State Prison：Washington State Prison is located in Davisboro in Washington County, Georgia. It is a facility for a maximum of 1548 adult male inmates at minimum and medium security. It is a part of the Georgia Department of Corrections.
- 原支持 `s-b1079ad304a46ed19452f33b` · List of municipalities in Georgia (U.S. state)：The largest municipality by population in Georgia is Atlanta with 420,003 residents, and the smallest municipality by population is Edge Hill with 24 residents. The largest municipality by land area is Augusta, a consolidated city - county, which spans 302.47 …
- 原支持 `s-66eb41261c0254cd99ebda50` · Sno*Drift：Sno*Drift is a rally racing event held in Montmorency County, Michigan, annually, with headquarters in Atlanta, Michigan. The event is currently the first Rally America National Rally Championship event of the season. Currently the event is organized into thre…
- 原支持 `s-4e5908f8ec48122bd646925c` · Alpena Power Company：Alpena Power Company is a public utility company that provides electricity to most of Alpena County, Michigan, as well as southeastern Presque Isle County and the village of Hillman, in Montmorency County. The headquarters are located in Alpena, Michigan. The …

## 291 · musique · musique:4hop1__166471_49925_13759_736921:answerable

**原题：** In what state is the district where the one who wanted to reform and address the institution behind the religion of Egidio Vagnozzi preached a sermon?

**原金标：** `[["Saxony-Anhalt"]]`；原标可答。

**问题类型：** `nonunique_reference`, `missing_reference_bridge`。

**裁定：** Many reformers preached sermons in many districts; neither a named reformer/sermon nor the Wittenberg sermon-location bridge is provided.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-f01887f82bff0b91395b9f34` · Wittenberg (district)：Wittenberg is a district () in the east of Saxony-Anhalt, Germany. Neighboring districts are (from west clockwise) Anhalt-Bitterfeld, the district-free city of Dessau-Roßlau, the districts of Potsdam-Mittelmark, Teltow-Fläming and Elbe-Elster in Brandenburg, a…
- 原支持 `s-26d7183c593f058ab8bb0b17` · Reformation：Although there had been significant earlier attempts to reform the Catholic Church before Luther -- such as those of Jan Hus, Peter Waldo, and John Wycliffe -- Martin Luther is widely acknowledged to have started the Reformation with the Ninety - five Theses. …
- 原支持 `s-50da1d1dee5408c1af971c1e` · Egidio Vagnozzi：Egidio Vagnozzi (26 February 1906 – 26 December 1980) was an Italian Cardinal of the Catholic Church. He served as the second president of the Prefecture for the Economic Affairs of the Holy See from 1968 until his death, and was elevated to the cardinalate in…
- 原支持 `s-95dab1be6ae3b10aaa008f24` · Mary, mother of Jesus：Despite Martin Luther's harsh polemics against his Roman Catholic opponents over issues concerning Mary and the saints, theologians appear to agree that Luther adhered to the Marian decrees of the ecumenical councils and dogmas of the church. He held fast to t…

## 292 · musique · musique:4hop1__51465_53706_795904_580996:answerable

**原题：** What shares a border with the city where the person who went to the state where most billionaires in America live during the gold rush works?

**原金标：** `[["Rio Linda"]]`；原标可答。

**问题类型：** `nonunique_reference`, `missing_reference_bridge`。

**裁定：** Many people went to California during the gold rush. Gold’s Rio Linda school paragraph neither identifies the person’s workplace nor establishes a city border.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-d6bcb8341c5d59d1c64cf6dc` · History of Sacramento, California：The history of Sacramento, California, began with its founding by Samuel Brannan and John Augustus Sutter, Jr. in 1848 around an embarcadero that his father, John Sutter, Sr. constructed at the confluence of the American and Sacramento Rivers a few years prior…
- 原支持 `s-37b335ff342bf57134c85029` · California Gold Rush：Rumors of the discovery of gold were confirmed in March 1848 by San Francisco newspaper publisher and merchant Samuel Brannan. Brannan hurriedly set up a store to sell gold prospecting supplies, and walked through the streets of San Francisco, holding aloft a …
- 原支持 `s-a79272429d3af57a5b186736` · List of U.S. states by the number of billionaires：Rank State / Region Number of billionaires California 124 New York 93 Florida 44 5 Illinois 17 Texas 48 5 Connecticut 17 7 Washington 13 11 Pennsylvania 10 15 Wisconsin 9 13 Arizona 9 25 Virginia 5 23 Minnesota 5 9 Colorado 10 10 Massachusetts 10 29 North Caro…
- 原支持 `s-2a783effa63c75e8e6c4310d` · Rio Linda High School：Rio Linda High School is a high school located in Rio Linda, Sacramento, CA. It has an enrollment of 2,035 students. It is part of the Twin Rivers Unified School District, and was formerly part of the Grant Unified School District.

## 293 · musique · musique:4hop1__56226_32392_823060_610794:answerable

**原题：** In which county is the city sharing a border with the capitol of the state that won the Battle of Sullivan's Island?

**原金标：** `[["Richland County"], ["Richland County, South Carolina"]]`；原标可答。

**问题类型：** `nonunique_reference`。

**裁定：** Multiple cities border Columbia and need not all be in one county; the question does not identify which neighboring city. Gold selects Forest Acres/Richland.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-c55a5daeda5e7e9323e40081` · Charleston, South Carolina：Although the city lost the status of state capital to Columbia in 1786, Charleston became even more prosperous in the plantation-dominated economy of the post-Revolutionary years. The invention of the cotton gin in 1793 revolutionized the processing of this cr…
- 原支持 `s-ca6135984baa5cc2df203471` · Forest Acres, South Carolina：Forest Acres is a city in Richland County, South Carolina, United States. The population was 10,361 at the 2010 census. It is part of the Columbia, South Carolina, Metropolitan Statistical Area.
- 原支持 `s-c690b2dc2a3aa3fd2ab98bc9` · Battle of Sullivan's Island：Battle of Sullivan's Island Part of the American Revolutionary War Sergeant William Jasper raising the flag over the fort, painted by Johannes Adam Simon Oertel, 1858 Date June 28, 1776 Location Sullivan's Island, South Carolina 32 ° 45 ′ 32 ''N 79 ° 51 ′ 28''…
- 原支持 `s-c754af6b5781fa7f19e093af` · WWNQ：WWNQ is a radio station licensed to Forest Acres, South Carolina, serving the Columbia, South Carolina market. Owned by Midlands Media Group LLC, the station broadcasts a country music format branded as 94.3 The Dude.

## 294 · musique · musique:4hop1__58323_375563_161848_83118:answerable

**原题：** When does daylight savings end in the country near the country where the author of The Book Thief is from?

**原金标：** `[["the first Sunday in April"]]`；原标可答。

**问题类型：** `nonunique_reference`。

**裁定：** A country “near” the author’s country does not uniquely identify New Zealand; the question omits which neighboring country and reference year.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-4ca969374202f1396373ed56` · The Messenger (Zusak novel)：The Messenger is a 2002 Novel by Markus Zusak, and winner of the 2003 Children's Book Council of Australia Book of the Year Award.
- 原支持 `s-4605e73d746e7d4108c1ff76` · Time in New Zealand：During summer months -- from the last Sunday in September until the first Sunday in April -- daylight saving time is observed and clocks are advanced one hour. New Zealand Daylight Time (NZDT) is 13 hours ahead of UTC, and Chatham Daylight Time (CHADT) 13 hour…
- 原支持 `s-c39e821f6b3b91c9f6c9e668` · The Book Thief (film)：The Book Thief is a 2013 World War II war drama film directed by Brian Percival and starring Geoffrey Rush, Emily Watson, and Sophie Nélisse. The film is based on the 2005 novel The Book Thief by Markus Zusak and adapted by Michael Petroni. The film is about a…
- 原支持 `s-f42fdb835501de6a0cc0ff55` · 1952 Winter Olympics：Thirty nations sent competitors, which was the highest number of participants at a Winter Games. New Zealand and Portugal took part in the Winter Olympic Games for the first time. Australia, Germany, and Japan returned after a 16-year absence. South Korea, Lie…

## 296 · musique · musique:4hop1__752321_153080_159767_81096:answerable

**原题：** The largest city in the state the performer of Pithecanthropus Erectus was from is also the state capital. Who won the indy car race in that city?

**原金标：** `[["Mario Andretti"]]`；原标可答。

**问题类型：** `temporal_scope_ambiguity`。

**裁定：** Phoenix Indy races have many editions/winners, including the 2016 race and Andretti’s 1993 victory in the reference. No year is specified; the gold is one selected winner.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-61c589da6ce2a154304dbf42` · Pithecanthropus Erectus (album)：Pithecanthropus Erectus is a 1956 album by jazz composer and bassist Charles Mingus. Mingus noted that this was the first album where he taught arrangements to his musicians by ear in lieu of putting the chords and arrangements in writing.
- 原支持 `s-c0a2caf4cf97b48e813a68ee` · Charles Mingus：Charles Mingus was born in Nogales, Arizona. His father, Charles Mingus Sr., was a sergeant in the U.S. Army. Mingus was largely raised in the Watts area of Los Angeles. His maternal grandfather was a Chinese British subject from Hong Kong, and his maternal gr…
- 原支持 `s-f2040f16858e656f0450ea4b` · Arizona：Arizona ( (listen); Navajo: Hoozdo Hahoodzo Navajo pronunciation: [xòːztò xɑ̀xòːtsò]; O'odham: Alĭ ṣonak Uto-Aztecan pronunciation: [ˡaɺi ˡʂonak]) is a state in the southwestern region of the United States. It is also part of the Western and the Mountain state…
- 原支持 `s-ea11dba55777060a896fab06` · Desert Diamond West Valley Phoenix Grand Prix：After a hiatus of eleven years, the race was revived by the Verizon IndyCar Series in 2016. It was held on Saturday night under the lights. Long considered a popular Indy car track, Phoenix has a rich history of open wheel races, including a spectacular crash …

## 297 · musique · musique:4hop1__860115_798482_131926_87157:unanswerable

**原题：** What is the direction of flow of the body of water by the city sharing a border with Elizabeth Berg's birthplace?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `missing_reference_chain`, `nonunique_reference`。

**裁定：** No uniquely named bordering city or river is specified; the local reference lacks the chain and does not establish global unanswerability.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-3d570c39d6aad72d99fe83e4` · Berg Bay：Berg Bay () is a small bay between Birthday Point and Islands Point in the west side of Robertson Bay, northern Victoria Land, Antarctica. The bay was charted and named in 1911 by the Northern Party led by Victor Campbell of the British Antarctic Expedition, 1…
- 原上下文 `s-80356df1a1bbf372ef07e936` · Henry of Berg, Lord of Windeck：Henry of Berg, Lord of Windeck (bef. 1247 – 8 March 1290/96) was the son of Adolf VII of Berg and Margaret of Hochstaden. He was the younger brother of Adolf VIII of Berg and William I of Berg.
- 原上下文 `s-f229df8e618244ba5335a3e5` · Valdes Peninsula：The coastline is inhabited by marine mammals, like sea lions, elephant seals and fur seals. Southern right whales can be found in Golfo Nuevo and Golfo San José, protected bodies of water located between the peninsula and the Patagonian mainland. These baleen …
- 原上下文 `s-4d42cf4177b48211f198b43d` · 1926 Men's European Water Polo Championship：The 1926 Men's European Water Polo Championship was the 1st edition of the event, organised by the Europe's governing body in aquatics, the Ligue Européenne de Natation. The event took place between 18–22 August in Budapest, Hungary as an integrated part of th…
- 其余 16 个证据单元见完整 JSON 账本。

## 298 · musique · musique:4hop1__88342_75218_128008_46894:unanswerable

**原题：** Who had the lowest batting average in the league where the team with the most games in the series after which the MLB MVP is awarded played?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `temporal_scope_ambiguity`。

**裁定：** Lowest batting average needs a season/career and qualification threshold; the team/league chain and scope are not established by original reference.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-ba08d223c6f4de32a0163d53` · Frank Kudelka：Frank Carl Kudelka (June 25, 1925 - May 4, 1993), nicknamed Apples, was an American National Basketball Association (NBA) player. In his first two years at Saint Mary's College of California, Kudelka averaged 18.1 points per game. Once the shotclock was introd…
- 原上下文 `s-3384bccea1a5268c8da6cc22` · 2017 ICC Champions Trophy Final：In an unexpected performance, Pakistan beat India comfortably to win their maiden ICC Champions Trophy, outclassing them across all departments to win by 180 runs, which was the largest margin of victory in the final of an ICC ODI tournament. Pakistan, who wer…
- 原上下文 `s-db8f5cee44701e5a6641e01b` · The Legend of Zelda: Twilight Princess：Twilight Princess received the awards for Best Artistic Design, Best Original Score, and Best Use of Sound from IGN for its GameCube version. Both IGN and Nintendo Power gave Twilight Princess the awards for Best Graphics and Best Story. Twilight Princess rece…
- 原上下文 `s-d510c20d6f85f5e8e3830480` · List of New York Yankees team records：Outfielder Babe Ruth holds the most franchise records, with 16, including career home runs, and career and single - season batting average and on - base percentage. Shortstop Derek Jeter has the second-most records among hitters, with eight. Jeter's marks incl…
- 其余 16 个证据单元见完整 JSON 账本。

## 299 · musique · musique:4hop1__88342_75218_128008_86588:answerable

**原题：** What is the longest home run in the history of the league of the team that has played the most games in the competition they give out MVP awards after?

**原金标：** `[["582 feet (177 m)"]]`；原标可答。

**问题类型：** `gold_league_conflation`。

**裁定：** 582 feet belongs to Joey Meyer in minor-league professional baseball; Yankees compete in MLB/AL, for which the same reference states Babe Ruth’s 575 feet. Gold mixes league levels.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-690c8d8b991863e76e955a8f` · Home run：Other legendary home run hitters include Jimmie Foxx, Mel Ott, Ted Williams, Mickey Mantle (who on September 10, 1960, mythically hit ``the longest home run ever ''at an estimated distance of 643 feet (196 m), although this was measured after the ball stopped …
- 原支持 `s-4c57334edb84845281a43d64` · New York Yankees：The New York Yankees are an American professional baseball team based in the New York City borough of the Bronx. The Yankees compete in Major League Baseball (MLB) as a member club of the American League (AL) East division. They are one of two major league clu…
- 原支持 `s-48b93e348026d803dce41433` · Major League Baseball Most Valuable Player Award：MVP voting takes place before the postseason, but the results are not announced until after the World Series. The BBWAA began by polling three writers in each league city in 1938, reducing that number to two per league city in 1961. The BBWAA does not offer a …
- 原支持 `s-1ac7e0c68778c7a57b2b9090` · World Series：In the American League, the New York Yankees have played in 40 World Series and won 27, the Philadelphia / Kansas City / Oakland Athletics have played in 14 and won 9, and the Boston Red Sox have played in 12 and won 8, including the first World Series. In the…

## 300 · musique · musique:4hop1__88342_75218_128008_92225:answerable

**原题：** When is the opening day of the league, that includes the team that played in the most games. of the event that the MLB MVP award is handed out after?

**原金标：** `[["March 29, 2018"]]`；原标可答。

**问题类型：** `temporal_scope_ambiguity`。

**裁定：** No season is named; generic early-April opening and a specific March 29, 2018 opening are different granularities/scopes. Gold arbitrarily fixes 2018.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-35d9c39feb36710471559f6f` · 2018 Major League Baseball season：The 2018 Major League Baseball season began on March 29, 2018, and is scheduled to end on September 30. The Postseason will begin on October 2. The 2018 World Series is set to begin on October 23, and a potential Game 7 is scheduled on October 31.
- 原支持 `s-4c57334edb84845281a43d64` · New York Yankees：The New York Yankees are an American professional baseball team based in the New York City borough of the Bronx. The Yankees compete in Major League Baseball (MLB) as a member club of the American League (AL) East division. They are one of two major league clu…
- 原支持 `s-48b93e348026d803dce41433` · Major League Baseball Most Valuable Player Award：MVP voting takes place before the postseason, but the results are not announced until after the World Series. The BBWAA began by polling three writers in each league city in 1938, reducing that number to two per league city in 1961. The BBWAA does not offer a …
- 原支持 `s-1ac7e0c68778c7a57b2b9090` · World Series：In the American League, the New York Yankees have played in 40 World Series and won 27, the Philadelphia / Kansas City / Oakland Athletics have played in 14 and won 9, and the Boston Red Sox have played in 12 and won 8, including the first World Series. In the…

## 301 · musique · musique:4hop2__161602_426860_88460_18966:answerable

**原题：** Who brought the language Hokkien to the country between the country that hosted the tournament and the country where That Dam is from?

**原金标：** `[["Han Chinese emigrants"], ["Han"], ["Han Chinese"]]`；原标可答。

**问题类型：** `scope_dependent`, `spatial_premise_error`。

**裁定：** “The tournament” is unnamed. The provided qualifier source identifies final hosts Thailand, and Thailand and Laos share a border; Myanmar is not a uniquely intermediate country.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-4fee84ba64a12bd4b050a4e6` · 2020 AFC U-23 Championship qualification：Of the 47 AFC member associations, a total of 44 teams entered the competition. The final tournament hosts Thailand decided to participate in qualification despite having automatically qualified for the final tournament.
- 原支持 `s-71ea446352154229eb56ec0b` · That Dam：That Dam (Lao ທາດດຳ, meaning Black Stupa) is a large stupa located in Vientiane, Laos. Many Laotians believe it is inhabited by a seven-headed nāga who tried to protect them from an invasion by the Siamese army in 1827.
- 原支持 `s-4bd37c03f31a9c416fadf750` · Hokkien：There are many Hokkien speakers among overseas Chinese in Southeast Asia as well as in the United States. Many ethnic Han Chinese emigrants to the region were Hoklo from southern Fujian, and brought the language to what is now Burma (Myanmar), Indonesia (the f…
- 原支持 `s-d21b3f1dcfdef8fb61005df1` · Geography of Myanmar：Myanmar (also known as Burma) is the northwestern-most country of mainland Southeast Asia, bordering China, India, Bangladesh, Thailand and Laos. It lies along the Indian and Eurasian Plates, to the southeast of the Himalayas. To its west is the Bay of Bengal …

## 302 · musique · musique:4hop2__161602_426860_88460_21062:answerable

**原题：** Are there any cruise travel destinations for the country lying between the country hosting the tournament and the country having That Dam?

**原金标：** `[["It is not possible for foreigners to go to/from Myanmar by sea or river."], ["Myanmar"], ["Burma"], ["MM"], ["BUR"], ["MYA"]]`；原标可答。

**问题类型：** `scope_dependent`, `spatial_premise_error`, `gold_incomplete`。

**裁定：** Unnamed tournament does not select a host; Thailand and Laos border each other. Gold also accepts bare Myanmar without answering the cruise question.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-71ea446352154229eb56ec0b` · That Dam：That Dam (Lao ທາດດຳ, meaning Black Stupa) is a large stupa located in Vientiane, Laos. Many Laotians believe it is inhabited by a seven-headed nāga who tried to protect them from an invasion by the Siamese army in 1827.
- 原支持 `s-4fee84ba64a12bd4b050a4e6` · 2020 AFC U-23 Championship qualification：Of the 47 AFC member associations, a total of 44 teams entered the competition. The final tournament hosts Thailand decided to participate in qualification despite having automatically qualified for the final tournament.
- 原支持 `s-d21b3f1dcfdef8fb61005df1` · Geography of Myanmar：Myanmar (also known as Burma) is the northwestern-most country of mainland Southeast Asia, bordering China, India, Bangladesh, Thailand and Laos. It lies along the Indian and Eurasian Plates, to the southeast of the Himalayas. To its west is the Bay of Bengal …
- 原支持 `s-0e675b8f608d2463f4e329b9` · Myanmar：The most common way for travellers to enter the country seems to be by air. According to the website Lonely Planet, getting into Myanmar is problematic: "No bus or train service connects Myanmar with another country, nor can you travel by car or motorcycle acr…

## 303 · musique · musique:4hop2__161602_474028_88460_21062:answerable

**原题：** Can foreign tourists cruise to the country between the nation that hosted 2020 AFC U-23 Championship qualification and the one that contains A Don.?

**原金标：** `[["It is not possible for foreigners to go to/from Myanmar by sea or river."], ["Myanmar"], ["Burma"], ["MM"], ["BUR"], ["MYA"]]`；原标可答。

**问题类型：** `spatial_premise_error`, `tournament_stage_conflation`, `gold_incomplete`。

**裁定：** Source identifies Thailand as final-tournament host, not sole qualification host. Thailand and Laos share a border, so the asserted intervening Myanmar is not established; bare country names also fail the requested yes/no.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-c2e84c96b5bb0a9a1454a888` · A Don：A Don is a village in south-eastern Laos near the border with Vietnam. It is located in Kaleum District in Sekong Province.
- 原支持 `s-0e675b8f608d2463f4e329b9` · Myanmar：The most common way for travellers to enter the country seems to be by air. According to the website Lonely Planet, getting into Myanmar is problematic: "No bus or train service connects Myanmar with another country, nor can you travel by car or motorcycle acr…
- 原支持 `s-d21b3f1dcfdef8fb61005df1` · Geography of Myanmar：Myanmar (also known as Burma) is the northwestern-most country of mainland Southeast Asia, bordering China, India, Bangladesh, Thailand and Laos. It lies along the Indian and Eurasian Plates, to the southeast of the Himalayas. To its west is the Bay of Bengal …
- 原支持 `s-4fee84ba64a12bd4b050a4e6` · 2020 AFC U-23 Championship qualification：Of the 47 AFC member associations, a total of 44 teams entered the competition. The final tournament hosts Thailand decided to participate in qualification despite having automatically qualified for the final tournament.

## 304 · musique · musique:4hop2__71753_158985_70784_61381:unanswerable

**原题：** When was the region immediately north of the region where Israel is located and the site of the most growth in desalination for agricultural use established?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `spatial_premise_error`。

**裁定：** North-of-Israel and desalination region clues do not establish the intended polity or creation date; original reference is incomplete.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-e098587578030c2cecaaa12c` · Human overpopulation：Almost all growth will take place in the less developed regions, where today's 5.3 billion population of underdeveloped countries is expected to increase to 7.8 billion in 2050. By contrast, the population of the more developed regions will remain mostly uncha…
- 原上下文 `s-7af1f65ff4e5c6f2d9784eeb` · Palouse：The Palouse ( ) is a distinct geographic region of the northwestern United States, encompassing parts of north central Idaho, southeastern Washington, and, by some definitions, parts of northeast Oregon. It is a major agricultural area, primarily producing whe…
- 原上下文 `s-5ff0ebaeb3844aaef91064a7` · Rajasthan：The Aravalli Range and the lands to the east and southeast of the range are generally more fertile and better watered. This region is home to the Kathiarbar-Gir dry deciduous forests ecoregion, with tropical dry broadleaf forests that include teak, Acacia, and…
- 原上下文 `s-c94e753d32c3be8779950e01` · Jequetepeque：Jequetepeque is a town in Northern Peru, capital of the district Jequetepeque in Pacasmayo Province of La Libertad Region. This town is located some 117 km north Trujillo city and is primarily an agricultural center in the Jequetepeque Valley.
- 其余 16 个证据单元见完整 JSON 账本。

## 305 · musique · musique:4hop2__9988_158279_70784_79935:answerable

**原题：** When was the region that lies to the north of what used to be known as the "Near East" and the location of the final destination of exported crops created?

**原金标：** `[["1930"]]`；原标可答。

**问题类型：** `spatial_premise_error`。

**裁定：** Target north of Near East/Persian Gulf is not established; gold follows Saudi Arabia from a source saying north of Yemen, with unrelated crop-export text.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-fb01f73ef3115585c99f1582` · Shiraz：Shiraz is the economic center of southern Iran. The second half of the 19th century witnessed certain economic developments that greatly changed the economy of Shiraz. The opening of the Suez Canal in 1869 allowed the extensive import into southern Iran of ine…
- 原支持 `s-c3121a56b6a0160e088249e4` · Geography of Saudi Arabia：The Kingdom of Saudi Arabia is a country situated in Southwest Asia, the largest country of Arabia, bordering the Persian Gulf and the Red Sea, north of Yemen. Its extensive coastlines on the Persian Gulf and Red Sea provide great leverage on shipping (especia…
- 原支持 `s-0a483c60733fc9b956a7c2fa` · History of Saudi Arabia：For much of the region's history a patchwork of tribal rulers controlled most of the area. The Al Saud (the Saudi royal family) emerged as minor tribal rulers in Najd in central Arabia. From the mid-18th century, imbued with the religious zeal of the Wahhabi I…
- 原支持 `s-2c0a6b207fe20b683a3d56de` · Near East：Subsequently with the disgrace of "Near East" in diplomatic and military circles, "Middle East" prevailed. However, "Near East" continues in some circles at the discretion of the defining agency or academic department. They are not generally considered distinc…

## 306 · musique · musique:4hop2__9988_261673_70784_61381:unanswerable

**原题：** The country in what was the Near East and bordering the sea over which shamal winds blow was established when?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `nonunique_reference`。

**裁定：** Several countries border the shamal/Persian Gulf region; the country and establishment event are not uniquely identified.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-583a221f334d20efdb73e9bb` · Acidalia Planitia：Acidalia Planitia is a plain on Mars. It is located between the Tharsis volcanic province and Arabia Terra to the north of Valles Marineris, centered at . Most of this region is found in the Mare Acidalium quadrangle, but a small part is in the Ismenius Lacus …
- 原上下文 `s-1e5076083beba07c4829aba4` · Alps：The Alps are a classic example of what happens when a temperate area at lower altitude gives way to higher-elevation terrain. Elevations around the world that have cold climates similar to those of the polar regions have been called Alpine. A rise from sea lev…
- 原上下文 `s-92c7eacdb13004812681be05` · Vigie Beach：Vigie Beach is located 2 km north of Castries, Saint Lucia, running parallel and immediately adjacent to the George F. L. Charles Airport.
- 原上下文 `s-76d21f02f25a7d05718e5649` · Zaranna：Zaranna is a village in the administrative district of Gmina Strzelce, within Kutno County, Łódź Voivodeship, in central Poland. It lies approximately north of Kutno and north of the regional capital Łódź.
- 其余 16 个证据单元见完整 JSON 账本。

## 307 · musique · musique:4hop3__152056_698586_91248_54362:answerable

**原题：** Who burned down the city where Dunn Dunn's artist died in the war after which the government got involved in healthcare?

**原金标：** `[["Confederate Gen. John Bell Hood"]]`；原标可答。

**问题类型：** `event_reference_ambiguity`。

**裁定：** Atlanta had distinct Civil War destruction events, including Hood’s September destruction in the cited paragraph and Sherman’s later burning. No date/event distinguishes them.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-9e7eec308b297c19f4f916da` · Atlanta in the American Civil War：In 1864, as feared by Jeremy F. Gilmer, Atlanta did indeed become the target of a major Union invasion. The area now covered by metropolitan Atlanta was the scene of several fiercely contested battles, including the Battle of Peachtree Creek, the Battle of Atl…
- 原支持 `s-ead3b39fce38b0b59563851e` · History of health care reform in the United States：After the Civil War, the federal government established the first system of medical care in the South, known as the Freedmen's Bureau. The government constructed 40 hospitals, employed over 120 physicians, and treated well over one million sick and dying forme…
- 原支持 `s-b43a71cbf97125e60ac95513` · Shawty Lo discography：The discography of Shawty Lo, an American hip hop recording artist from Atlanta, Georgia. Shawty Lo embarked on his career with the Southern hip hop group D4L. The discography consists of one studio album, one posthumous album, 15 mixtapes and 20 singles (incl…
- 原支持 `s-80047217feb09e55deb0dcb4` · Dunn Dunn："Dunn Dunn", produced by Born Immaculate and DJ Pooh, is the second single from Shawty Lo's debut solo album, "Units in the City". Part of Shawty Lo's third single, "Foolish," is played at the end. However, at the end of the video "to be continued" is seen on …

## 308 · musique · musique:4hop3__238811_88460_30152_20999:answerable

**原题：** New coins by the Somali Muslim Ajuran Empire were a proclamation of independence from a nation. How were the people of that nation expelled from country that borders Thailand and Ban Nampo village's country?

**原金标：** `[["The dynasty regrouped and defeated the Portuguese"]]`；原标可答。

**问题类型：** `nonunique_reference`。

**裁定：** More than one country borders Thailand and Laos; the question does not uniquely select Myanmar or the specific Portuguese expulsion event.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-312faa4701e10790114bc329` · Ottoman Empire：The discovery of new maritime trade routes by Western European states allowed them to avoid the Ottoman trade monopoly. The Portuguese discovery of the Cape of Good Hope in 1488 initiated a series of Ottoman-Portuguese naval wars in the Indian Ocean throughout…
- 原支持 `s-7caa5c7ed0bdfb20a43a0dd5` · Myanmar：The dynasty regrouped and defeated the Portuguese in 1613 and Siam in 1614. It restored a smaller, more manageable kingdom, encompassing Lower Myanmar, Upper Myanmar, Shan states, Lan Na and upper Tenasserim. The Restored Toungoo kings created a legal and poli…
- 原支持 `s-d21b3f1dcfdef8fb61005df1` · Geography of Myanmar：Myanmar (also known as Burma) is the northwestern-most country of mainland Southeast Asia, bordering China, India, Bangladesh, Thailand and Laos. It lies along the Indian and Eurasian Plates, to the southeast of the Himalayas. To its west is the Bay of Bengal …
- 原支持 `s-48076cddaf612163bb81b93e` · Ban Nampo：Ban Nampo is a village in Vientiane Province, Laos. It is located north along Route 13 from Vang Vieng, not far from the eastern bank of the Nam Song River. The Nam Po River flows into the Nam Song to the south. To the north is the village of Pak Pok.

## 309 · musique · musique:4hop3__310746_88460_30152_20999:answerable

**原题：** There exists a country A that forms a boundary between Thailand and the country containing the Khong District. How were the people that the Ajuran Empire declared independence from by minting coins expelled from country A?

**原金标：** `[["The dynasty regrouped and defeated the Portuguese"]]`；原标可答。

**问题类型：** `spatial_premise_error`。

**裁定：** Thailand and Laos share their own boundary; Myanmar does not form an intervening country boundary between them as asserted.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-7caa5c7ed0bdfb20a43a0dd5` · Myanmar：The dynasty regrouped and defeated the Portuguese in 1613 and Siam in 1614. It restored a smaller, more manageable kingdom, encompassing Lower Myanmar, Upper Myanmar, Shan states, Lan Na and upper Tenasserim. The Restored Toungoo kings created a legal and poli…
- 原支持 `s-27aa851abb0c04bee9ec3ca0` · Khong District, Laos：Khong is a district ("muang") of Champassack Province in southwestern Laos. The district borders Cambodia in the far south and is famous for the Khonephapheng Waterfalls and the Si Phan Don (4000 Islands) area.
- 原支持 `s-312faa4701e10790114bc329` · Ottoman Empire：The discovery of new maritime trade routes by Western European states allowed them to avoid the Ottoman trade monopoly. The Portuguese discovery of the Cape of Good Hope in 1488 initiated a series of Ottoman-Portuguese naval wars in the Indian Ocean throughout…
- 原支持 `s-d21b3f1dcfdef8fb61005df1` · Geography of Myanmar：Myanmar (also known as Burma) is the northwestern-most country of mainland Southeast Asia, bordering China, India, Bangladesh, Thailand and Laos. It lies along the Indian and Eurasian Plates, to the southeast of the Himalayas. To its west is the Bay of Bengal …

## 313 · musique · musique:4hop3__800262_466199_695123_72134:answerable

**原题：** When did the town WIZE is licensed in become capitol of the state where Green Township is located?

**原金标：** `[["1839"]]`；原标可答。

**问题类型：** `gold_entity_conflation`。

**裁定：** WIZE is in Springfield, Ohio; gold follows Springfield, Illinois via a separate Randolph County, although Green Township is in Randolph County, Indiana.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-41353bd3265535f0564bedba` · WIZE：WIZE (1340 AM) — branded WIZE AM 1340 — is a commercial radio station in Springfield, Ohio owned by iHeartMedia, Inc. as part of their Dayton cluster. The station's main format is classic country targeted towards Springfield, and their transmitter - and former…
- 原支持 `s-8bc89a00319fa6b289c65733` · Springfield, Illinois：Springfield's original name was Calhoun, after Senator John C. Calhoun of South Carolina. The land that Springfield now occupies was originally settled by trappers and traders who came to the Sangamon River in 1818. The settlement's first cabin was built in 18…
- 原支持 `s-6d6ab0782ba2c6ad5e6f31d0` · Green Township, Randolph County, Indiana：Green Township is one of eleven townships in Randolph County, Indiana. As of the 2010 census, its population was 957 and it contained 404 housing units.
- 原支持 `s-f732cef4037acdeab34e8800` · Randolph County, Illinois：Owing to its role in the state's history, the county motto is "Where Illinois Began." It contains the historically important village of Kaskaskia, Illinois's first capital.

## 320 · squad · squad:5705e63175f01819005e7721

**原题：** What is the population of Los Angeles?

**原金标：** `[["3,792,621"], ["3,792,621"], ["3,792,621"]]`；原标可答。

**问题类型：** `temporal_scope_ambiguity`。

**裁定：** Population count has no year or census qualifier; gold uses the 2010 census city population, while the corpus contains different dates and geographic units.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-6ad6809a2bf64e612e86a293` · Southern_California：Within southern California are two major cities, Los Angeles and San Diego, as well as three of the country's largest metropolitan areas. With a population of 3,792,621, Los Angeles is the most populous city in California and the second most populous in the Un…

## 322 · squad · squad:57060a6e52bb8914006897fa

**原题：** What is the only district in the CBD to not have "downtown" in it's name?

**原金标：** `[["South Coast Metro"], ["South Coast Metro"], ["South Coast Metro"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** “The CBD” has no city/region qualifier and cannot identify a unique set of districts in the pooled corpus.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-4d92f7ed67afe3a159c0aea3` · Southern_California：Southern California is home to many major business districts. Central business districts (CBD) include Downtown Los Angeles, Downtown San Diego, Downtown San Bernardino, Downtown Bakersfield, South Coast Metro and Downtown Riverside.

## 327 · squad · squad:5710f114a58dae1900cd6b62

**原题：** About how many Walloons and Huguenots emigrated to England and Ireland in this era?

**原金标：** `[["50,000"], ["50,000"], ["50,000"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** “This era” has no referent; reference anchors the 50,000 estimate around the 1708 naturalization act, which the pooled-corpus question omits.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-e5361f62e558e4996f646e91` · Huguenot：Both before and after the 1708 passage of the Foreign Protestants Naturalization Act, an estimated 50,000 Protestant Walloons and Huguenots fled to England, with many moving on to Ireland and elsewhere. In relative terms, this was one of the largest waves of i…

## 332 · squad · squad:571a53d410f8ca1400304fe7

**原题：** Oxygen exists in the atmosphere by way of what?

**原金标：** `[["carbon dioxide"], ["carbon dioxide"], ["carbon dioxide"], ["carbon dioxide"]]`；原标可答。

**问题类型：** `target_relation_ambiguity`。

**裁定：** “By way of what” does not distinguish atmospheric chemical form (mainly O2, also CO2) from oxygen production (photosynthesis); gold picks the trace CO2 sentence only.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-1f92363640bf91bfe9f18d75` · Oxygen：Oxygen is present in the atmosphere in trace quantities in the form of carbon dioxide (CO 2). The Earth's crustal rock is composed in large part of oxides of silicon (silica SiO 2, as found in granite and quartz), aluminium (aluminium oxide Al 2O 3, in bauxite…

## 339 · squad · squad:5726487b5951b619008f6edf

**原题：** Which country is a traditional ally of Israel?

**原金标：** `[["UK"], ["UK"], ["The UK"], ["The UK"]]`；原标可答。

**问题类型：** `nonunique_reference`。

**裁定：** The question asks for a traditional ally without date or a discriminating relation; the UK is one example, and other named allies can also be correct.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-559c3a2356e19e4d08fadb8f` · 1973_oil_crisis：The embargo was not uniform across Europe. Of the nine members of the European Economic Community (EEC), the Netherlands faced a complete embargo, the UK and France received almost uninterrupted supplies (having refused to allow America to use their airfields …

## 341 · squad · squad:57264d58f1498d1400e8db7a

**原题：** What is the mortality rate of the modern bubonic plague?

**原金标：** `[["30–75%"], ["30–75%"], ["30–75%"]]`；原标可答。

**问题类型：** `treatment_scope_ambiguity`。

**裁定：** Mortality depends on treatment/population/period; question names none. The historical source also gives a separate untreated figure and does not support a universal modern rate.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-c96aef7a6ab6736228560949` · Black_Death：Other forms of plague have been implicated by modern scientists. The modern bubonic plague has a mortality rate of 30–75% and symptoms including fever of 38–41 °C (100–106 °F), headaches, painful aching joints, nausea and vomiting, and a general feeling of mal…

## 347 · squad · squad:5726a14c708984140094cc52

**原题：** Which laws mentioned predate EU law?

**原金标：** `[["international law and public law"], ["international law and public law"], ["international law and public law"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** “Laws mentioned” does not specify the passage or legal fields/principles; many legal systems and principles predate EU law, so an unrestricted pooled-corpus question has no unique target list.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-3c9c32b6e54eeafb76fc180b` · European_Union_law：The concept of legal certainty is recognised one of the general principles of European Union law by the European Court of Justice since the 1960s. It is an important general principle of international law and public law, which predates European Union law. As a…

## 351 · squad · squad:5726f0e35951b619008f82fa

**原题：** Has there ever been anyone charged with importing drugs from Canada for personal medicinal use?

**原金标：** `[["There is no known case"], ["no known case"], ["no"]]`；原标可答。

**问题类型：** `temporal_jurisdiction_scope_ambiguity`。

**裁定：** “Has there ever” lacks an as-of date and jurisdiction; gold is a dated no-known-US-case claim from the reference, not a verified universal negative.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-9dd43323e222f013239c03c8` · Pharmacy：In the United States, there has been a push to legalize importation of medications from Canada and other countries, in order to reduce consumer costs. While in most cases importation of prescription medications violates Food and Drug Administration (FDA) regul…

## 353 · squad · squad:57274d9bf1498d1400e8f5f6

**原题：** In what year did Malaysia receive its independence?

**原金标：** `[["1957"], ["1957"], ["1957"]]`；原标可答。

**问题类型：** `historical_entity_ambiguity`。

**裁定：** 1957 independence concerns the Federation of Malaya, whereas Malaysia was formed in 1963; the question’s unqualified entity/event can be interpreted differently.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-9ab564834bccc54aaca56fc0` · Private_school：After Malaysia's independence in 1957, the government instructed all schools to surrender their properties and be assimilated into the National School system. This caused an uproar among the Chinese and a compromise was achieved in that the schools would inste…

## 355 · squad · squad:57287e512ca10214002da3fb

**原题：** The mother of which emperor was a concubine?

**原金标：** `[["Tugh Temur"], ["Tugh Temur"], ["Tugh Temur"]]`；原标可答。

**问题类型：** `nonunique_reference`。

**裁定：** Many emperors had mothers who were concubines; no dynasty or other identifying condition is supplied. Gold’s Tugh Temur is one example, not a unique answer.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-d0b5f3336b003577d30256c3` · Yuan_dynasty：Since its invention in 1269, the 'Phags-pa script, a unified script for spelling Mongolian, Tibetan, and Chinese languages, was preserved in the court until the end of the dynasty. Most of the Emperors could not master written Chinese, but they could generally…

## 356 · squad · squad:57287f6a3acd2414000dfa52

**原题：** Which arts were often practiced together by the same artists?

**原金标：** `[["painting, poetry, and calligraphy"], ["painting, poetry, and calligraphy"], ["painting, poetry, and calligraphy"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** Question omits era/culture/artist set; gold refers to Chinese painting/poetry/calligraphy, while “arts often practiced together” is broader in a pooled corpus.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-861bbbf2104bbf0fa90d592d` · Yuan_dynasty：In the China of the Yuan, or Mongol era, various important developments in the arts occurred or continued in their development, including the areas of painting, mathematics, calligraphy, poetry, and theater, with many great artists and writers being famous tod…

## 359 · squad · squad:572982e66aef051400154f95

**原题：** What organization offers monetary awards for identifying primes with at least 100 million digits?

**原金标：** `[["The Electronic Frontier Foundation"], ["Electronic Frontier Foundation"], ["Electronic Frontier Foundation"], [". The Electronic Frontier Foundation"], ["$150,000"]]`；原标可答。

**问题类型：** `gold_target_type_error`。

**裁定：** One gold alternative is $150,000, a prize amount rather than the requested organization; correct organization is EFF.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-ab8304ca39d4c5f523ea33fb` · Prime_number：The following table gives the largest known primes of the mentioned types. Some of these primes have been found using distributed computing. In 2009, the Great Internet Mersenne Prime Search project was awarded a US$100,000 prize for first discovering a prime …

## 364 · squad · squad:572fc6f204bcaa1900d76cf9

**原题：** Where might committees meet outside of Parliament?

**原金标：** `[["other locations throughout Scotland"], ["other locations throughout Scotland"], ["other locations throughout Scotland"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** Question does not identify which Parliament or its committees; Scottish Parliament is supplied only by the source context.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-b447a4436eb39cbae9e0941b` · Scottish_Parliament：Much of the work of the Scottish Parliament is done in committee. The role of committees is stronger in the Scottish Parliament than in other parliamentary systems, partly as a means of strengthening the role of backbenchers in their scrutiny of the government…

## 365 · squad · squad:572fcb6da23a5019007fc9f2

**原题：** If a matter is not specifically reserved, who is it devolved to?

**原金标：** `[["Scottish Parliament"], ["Scottish Parliament"], ["the Scottish Parliament"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** Reserved/devolved matters require a named constitutional jurisdiction. Scottish Parliament is implied only by the source context.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-d2854474b7c6db9bbae8ae90` · Scottish_Parliament：The specific devolved matters are all subjects which are not explicitly stated in Schedule 5 to the Scotland Act as reserved matters. All matters that are not specifically reserved are automatically devolved to the Scottish Parliament. Most importantly, this i…

## 366 · squad · squad:572fdd03a23a5019007fca9f

**原题：** What phenomenon has led to criticism?

**原金标：** `[["West Lothian question"], ["West Lothian question"], ["the West Lothian question"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** No topic, institution, policy or criticism is named, so many phenomena fit; West Lothian question is available only from hidden paragraph context.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-b4a454b620db1228fe93e2a1` · Scottish_Parliament：A procedural consequence of the establishment of the Scottish Parliament is that Scottish MPs sitting in the UK House of Commons are able to vote on domestic legislation that applies only to England, Wales and Northern Ireland – whilst English, Scottish, Welsh…

## 369 · squad · squad:5733f5264776f419006615a3

**原题：** What political response was convening in June/July 1754?

**原金标：** `[["Albany Congress"], ["Albany Congress"], ["the opening of hostilities"], ["Albany Congress"], ["Albany Congress"]]`；原标可答。

**问题类型：** `gold_target_type_error`。

**裁定：** Gold also accepts “the opening of hostilities”, which is not the named political meeting/response; the meeting is Albany Congress.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-57d556fd2ad38ee26c43c618` · French_and_Indian_War：An early important political response to the opening of hostilities was the convening of the Albany Congress in June and July, 1754. The goal of the congress was to formalize a unified front in trade and negotiations with various Indians, since allegiance of t…

## 370 · squad · squad:573403394776f419006616e0

**原题：** What impact did this loss have on Abercrombie?

**原金标：** `[["recalled and replaced by Jeffery Amherst, victor at Louisbourg."], ["Abercrombie was recalled and replaced"], ["was recalled and replaced by Jeffery Amherst"], ["recalled and replaced"], ["Abercrombie was recalled and replaced by Jeffery Amherst,"]]`；原标可答。

**问题类型：** `scope_dependent`。

**裁定：** “This loss” is not identified in the question; recall after the Carillon defeat is supplied by the paragraph context.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-17c1e575583c01a2d2823e0c` · French_and_Indian_War：The third invasion was stopped with the improbable French victory in the Battle of Carillon, in which 3,600 Frenchmen famously and decisively defeated Abercrombie's force of 18,000 regulars, militia and Native American allies outside the fort the French called…

## 373 · squad · squad:5737a7351c456719005744f3

**原题：** What is preserved in a closed system of forces when acted upon?

**原金标：** `[["net mechanical energy"], ["net mechanical energy"], ["net mechanical energy"], ["net mechanical energy"]]`；原标可答。

**问题类型：** `conservation_conditions_missing`。

**裁定：** A closed system conserves momentum/total energy; mechanical energy additionally requires conservative forces. Question omits which forces/condition and gold selects net mechanical energy.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原支持 `s-c488c795cc289ed7a71d4df1` · Force：A conservative force that acts on a closed system has an associated mechanical work that allows energy to convert only between kinetic or potential forms. This means that for a closed system, the net mechanical energy is conserved whenever a conservative force…

## 374 · squad · squad:5a0c807ff5590b0018dab450

**原题：** Prior to 1960 what method was used to clear forest?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** Question omits the forest/location and original text does not identify the pre-1960 method; local absence is not a full-corpus negative. Global answerability/unanswerability has not been adjudicated from a complete reference.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-db40da38b67f4fd9d8feb2c7` · Amazon_rainforest：Deforestation is the conversion of forested areas to non-forested areas. The main sources of deforestation in the Amazon are human settlement and development of the land. Prior to the early 1960s, access to the forest's interior was highly restricted, and the …

## 375 · squad · squad:5a25bd5cef59cd001a623cc7

**原题：** How long does it take to build electrical facilities?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** Facility type/scale/location are unspecified. Building a small 3D-printed house does not determine construction time for electrical facilities. Global answerability/unanswerability has not been adjudicated from a complete reference.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-22d2534af54ce6d70c1ace74` · Construction：New techniques of building construction are being researched, made possible by advances in 3D printing technology. In a form of additive building construction, similar to the additive manufacturing techniques for manufactured parts, building printing is making…

## 376 · squad · squad:5a2c1397bfd06b001a5ae9cd

**原题：** What was the UK governments benefits agency destroying in 2012?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** Question substitutes destroying for checking claimants’ bills; source does not establish a destruction event. Global answerability/unanswerability has not been adjudicated from a complete reference.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-d803d1cda121ad6b7fa489ba` · Sky_(United_Kingdom)：The Daily Mail newspaper reported in 2012 that the UK government's benefits agency was checking claimants' "Sky TV bills to establish if a woman in receipt of benefits as a single mother is wrongly claiming to be living alone" – as, it claimed, subscription to…

## 377 · squad · squad:5a2ebaada83784001a7d2441

**原题：** What was one of the countries that had isolated alpine villages?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** Many Alpine countries have isolated villages; an empty local gold cannot make factual Switzerland/Italy answers semantically false. Global answerability/unanswerability has not been adjudicated from a complete reference.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-ed91333755622ff12713ef01` · Black_Death：From Italy, the disease spread northwest across Europe, striking France, Spain, Portugal and England by June 1348, then turned and spread east through Germany and Scandinavia from 1348 to 1350. It was introduced in Norway in 1349 when a ship landed at Askøy, t…

## 378 · squad · squad:5a38b9b3a4b263001a8c18d1

**原题：** Who did the EEC support during the Six-Day War?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** Reference does not establish a collective EEC position; individual UK support is not the same organization’s policy. Global answerability/unanswerability has not been adjudicated from a complete reference.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-559c3a2356e19e4d08fadb8f` · 1973_oil_crisis：The embargo was not uniform across Europe. Of the nine members of the European Economic Community (EEC), the Netherlands faced a complete embargo, the UK and France received almost uninterrupted supplies (having refused to allow America to use their airfields …

## 379 · squad · squad:5a38ceaaa4b263001a8c1936

**原题：** What did some buyers not like about Acuras?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** Original passage concerns objections to earlier small Japanese compacts and subsequent luxury lines; it does not establish a unique Acura buyer complaint. Global answerability/unanswerability has not been adjudicated from a complete reference.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-e5b56b83d2a3a5710222dc64` · 1973_oil_crisis：Some buyers lamented the small size of the first Japanese compacts, and both Toyota and Nissan (then known as Datsun) introduced larger cars such as the Toyota Corona Mark II, the Toyota Cressida, the Mazda 616 and Datsun 810, which added passenger space and a…

## 380 · squad · squad:5a3e3de4378766001a002514

**原题：** Who should be concerned with the trend to under state the effects of climate change?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** Who should be concerned is a normative open question with many valid stakeholders, not a unique factual name. Global answerability/unanswerability has not been adjudicated from a complete reference.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-ad74f92217d8c8b626b408ac` · Intergovernmental_Panel_on_Climate_Change：Former IPCC chairman Robert Watson has said "The mistakes all appear to have gone in the direction of making it seem like climate change is more serious by overstating the impact. That is worrying. The IPCC needs to look at this trend in the errors and ask why…

## 381 · squad · squad:5a3e44ea378766001a002533

**原题：** Who suggested that the investigation was legitimate?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** “The investigation” is unnamed; reference may distinguish supporters from critics, and no unique event is fixed by the pooled question. Global answerability/unanswerability has not been adjudicated from a complete reference.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-1246508bbabc7040f900d17d` · Intergovernmental_Panel_on_Climate_Change：On 23 June 2005, Rep. Joe Barton, chairman of the House Committee on Energy and Commerce wrote joint letters with Ed Whitfield, Chairman of the Subcommittee on Oversight and Investigations demanding full records on climate research, as well as personal informa…

## 382 · squad · squad:5a581bda770dc0001aef0004

**原题：** Why did the Royal Society think the Earth was older than previously thought?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** The source attributes geological reasoning to Hutton’s paper presented to a Royal Society, not a stated institutional belief. The question changes the actor. Global answerability/unanswerability has not been adjudicated from a complete reference.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-95c394591764975fe0bf222e` · Geology：James Hutton is often viewed as the first modern geologist. In 1785 he presented a paper entitled Theory of the Earth to the Royal Society of Edinburgh. In his paper, he explained his theory that the Earth must be much older than had previously been supposed i…

## 383 · squad · squad:5a592b593e1742001a15cfcd

**原题：** What helps scientists to date fossils?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** Fossil dating has multiple legitimate methods; original empty gold reflects paragraph-local answerability and cannot invalidate factual dating methods. Global answerability/unanswerability has not been adjudicated from a complete reference.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-7f32657f9e6f38b79f0b285a` · Geology：In the laboratory, biostratigraphers analyze rock samples from outcrop and drill cores for the fossils found in them. These fossils help scientists to date the core and to understand the depositional environment in which the rock units formed. Geochronologists…

## 384 · squad · squad:5a6059ace9e1cc001a33cd38

**原题：** Was the RAND corporation prohibited from disclosing the research to anyone else except for the Air Force?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** “The research” and its date/classification are unspecified; the reference does not establish a disclosure prohibition. Global answerability/unanswerability has not been adjudicated from a complete reference.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-38aa18f11493ea6b9f439b73` · Packet_switching：Baran developed the concept of distributed adaptive message block switching during his research at the RAND Corporation for the US Air Force into survivable communications networks, first presented to the Air Force in the summer of 1961 as briefing B-265, late…

## 385 · squad · squad:5a636c5a68151a001a9222c9

**原题：** How does Melbourne rank as to population?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** Population rank lacks a year and city/urban-area boundary definition; historical and later rankings can differ. Global answerability/unanswerability has not been adjudicated from a complete reference.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-4ab9c3d7254699d4e72fba7c` · Victoria_(Australia)：Victoria (abbreviated as Vic) is a state in the south-east of Australia. Victoria is Australia's most densely populated state and its second-most populous state overall. Most of its population is concentrated in the area surrounding Port Phillip Bay, which inc…

## 386 · squad · squad:5a637c6268151a001a922311

**原题：** What did some leaders of the British rebellion become?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** “British rebellion” changes the Eureka rebellion against British colonial government into an undefined British rebellion.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-30ff1c22c509b295c6208d17` · Victoria_(Australia)：In 1854 at Ballarat there was an armed rebellion against the government of Victoria by miners protesting against mining taxes (the "Eureka Stockade"). This was crushed by British troops, but the discontents prompted colonial authorities to reform the administr…

## 387 · squad · squad:5a63888168151a001a922344

**原题：** How many Victorians are Muslim?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `false_unanswerable_reference`, `temporal_scope_ambiguity`。

**裁定：** Original paragraph explicitly states 152,775 Muslims in Victoria, despite empty gold. The question still omits the census/year, so the fact is only snapshot-specific.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-befa817cab0740a999e034f7` · Victoria_(Australia)：About 61.1% of Victorians describe themselves as Christian. Roman Catholics form the single largest religious group in the state with 26.7% of the Victorian population, followed by Anglicans and members of the Uniting Church. Buddhism is the state's largest no…

## 388 · squad · squad:5a638d2768151a001a92236e

**原题：** How much Victorian farmland is farmed in rye grass?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** The source gives wheat, barley, oats and hay acreage, not rye grass; full-corpus rye-grass amount is unverified.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-c2c6ba1bdf5cf1e55090073c` · Victoria_(Australia)：More than 26,000 square kilometres (10,000 sq mi) of Victorian farmland are sown for grain, mostly in the state's west. More than 50% of this area is sown for wheat, 33% for barley and 7% for oats. A further 6,000 square kilometres (2,300 sq mi) is sown for ha…

## 389 · squad · squad:5a6696d0f038b7001ab0c020

**原题：** Telstra started what in the early 1980s?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `false_unanswerable_reference`。

**裁定：** AUSTPAC was started by Telecom Australia and operated by Telstra; those are historical names of the same organization. Empty gold rejects an answer supported by the paragraph.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-542f2947c8904347c64506ed` · Packet_switching：AUSTPAC was an Australian public X.25 network operated by Telstra. Started by Telecom Australia in the early 1980s, AUSTPAC was Australia's first public packet-switched data network, supporting applications such as on-line betting, financial applications — the…

## 390 · squad · squad:5a6696d0f038b7001ab0c023

**原题：** What was the Australian public X.75 network operated by Telstra?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** Question changes X.25 to X.75; AUSTPAC in the reference is explicitly X.25, so the requested network is not established.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-542f2947c8904347c64506ed` · Packet_switching：AUSTPAC was an Australian public X.25 network operated by Telstra. Started by Telecom Australia in the early 1980s, AUSTPAC was Australia's first public packet-switched data network, supporting applications such as on-line betting, financial applications — the…

## 391 · squad · squad:5a67e5278476ee001a58a764

**原题：** What were the operating expenses of the Groton School in 2012?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** The reference gives tuition and endowment, not 2012 Groton operating expenses; no full-corpus negative has been verified.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-71595d4d4be072aee58687cc` · Private_school：As of 2012, quality private schools in the United States charged substantial tuition, close to $40,000 annually for day schools in New York City, and nearly $50,000 for boarding schools. However, tuition did not cover operating expenses, particularly at boardi…

## 392 · squad · squad:5a75028897ca42001a521db3

**原题：** What caused the crisis?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** “The crisis” has no referent in the pooled corpus; oil, debt and credit crises are all present and have different causes.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-5e69aaee5d0c90fa1bf2c0d3` · 1973_oil_crisis：The 1973 oil crisis began in October 1973 when the members of the Organization of Arab Petroleum Exporting Countries (OAPEC, consisting of the Arab members of OPEC plus Egypt and Syria) proclaimed an oil embargo. By the end of the embargo in March 1974, the pr…

## 393 · squad · squad:5a838db9e60761001a2eb7b9

**原题：** What do most species of the genus Ocryopsis function as at the same time?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** The source says two Ocryopsis species remain single-sex; it does not establish that most Ocryopsis are simultaneously hermaphroditic. Generic ctenophore statement cannot be transferred to the exception genus.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-93f943d897751bdab34b26cc` · Ctenophora：Almost all species are hermaphrodites, in other words they function as both males and females at the same time – except that in two species of the genus Ocryopsis individuals remain of the same single sex all their lives. The gonads are located in the parts of…

## 394 · squad · squad:5a89150d3b2508001a72a4aa

**原题：** Besides the analytic property of Goldbach's conjecture, what other property of Goldbach's conjecture does number theory focus on?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** Source discusses analytic/algebraic aspects of numbers, not a distinct algebraic property of Goldbach’s conjecture; the question changes the target.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-85f340c1dbd179754f9d75c4` · Prime_number：Many questions regarding prime numbers remain open, such as Goldbach's conjecture (that every even integer greater than 2 can be expressed as the sum of two primes), and the twin prime conjecture (that there are infinitely many pairs of primes whose difference…

## 395 · squad · squad:5a8943753b2508001a72a570

**原题：** What type of insect employs the use of Magicicadas in its evolutionary strategy?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** The question reverses the relation, asking which insect uses Magicicadas rather than which insect uses prime-numbered life cycles. The universal negative “no insect” is also not established.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-a42cecdc8c3cbf31cdb4af94` · Prime_number：The evolutionary strategy used by cicadas of the genus Magicicada make use of prime numbers. These insects spend most of their lives as grubs underground. They only pupate and then emerge from their burrows after 7, 13 or 17 years, at which point they fly abou…

## 396 · squad · squad:5acfeee177cf76001a686573

**原题：** How many people did the Islamic State control the territory of as of February 2015?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** The source estimates ten million as of March 2015, whereas the question asks February 2015. That month-specific number is not established.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-65167dae84559181e98687e8` · Islamism："The Islamic State", formerly known as the "Islamic State of Iraq and the Levant" and before that as the "Islamic State of Iraq", (and called the acronym Daesh by its many detractors), is a Wahhabi/Salafi jihadist extremist militant group which is led by and m…

## 397 · squad · squad:5ad0278077cf76001a686be8

**原题：** French architect Enric Miralles helped design what building?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** The source identifies Miralles as Spanish, not French; the question has a false nationality premise even though the intended building is recoverable.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-d6facf4d0a881cfa8786ecd6` · Scottish_Parliament：Since September 2004, the official home of the Scottish Parliament has been a new Scottish Parliament Building, in the Holyrood area of Edinburgh. The Scottish Parliament building was designed by Spanish architect Enric Miralles in partnership with local Edinb…

## 398 · squad · squad:5ad03b0b77cf76001a686e93

**原题：** The strife between races in Canada is a unique form of what?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** Reference discusses internal colonialism in the United States, whereas the question substitutes Canada. Its attribution is not established by that paragraph.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-c86096d49aac018313a765de` · Imperialism：Some have described the internal strife between various people groups as a form of imperialism or colonialism. This internal form is distinct from informal U.S. imperialism in the form of political and financial hegemony. This internal form of imperialism is a…

## 399 · squad · squad:5ad0421177cf76001a686f16

**原题：** What economy started growing in the Greater Bay Region?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** “Greater Bay Region” is not the Greater Sacramento region or Bay Area in the reference; region/economy target is not established.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-be42ff249756bdae0f2c19ae` · Southern_California：As of the 2010 United States Census, southern California has a population of 22,680,010. Despite a reputation for high growth rates, southern California's rate grew less than the state average of 10.0% in the 2000s as California's growth became concentrated in…

## 400 · squad · squad:5ad044f977cf76001a686f61

**原题：** Current party leader, David McLetchie lost what seat?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** Source calls McLetchie former party leader and Goldie the leader, whereas question says current party leader. Seat is recoverable but premise is wrong.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-d98e4c927c5d3d1847a32e10` · Scottish_Parliament：For the Conservatives, the main disappointment was the loss of Edinburgh Pentlands, the seat of former party leader David McLetchie, to the SNP. McLetchie was elected on the Lothian regional list and the Conservatives suffered a net loss of five seats, with le…

## 401 · squad · squad:5ad15d77645df0001a2d18c1

**原题：** What did countries such as Spain have a crime against?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** Source explicitly says Spain did not have the stated toxic-waste crime, whereas the question asks what it had a crime against.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-5b551d2cb8c1a26ebe89151e` · European_Union_law：In 2006, a toxic waste spill off the coast of Côte d'Ivoire, from a European ship, prompted the Commission to look into legislation against toxic waste. Environment Commissioner Stavros Dimas stated that "Such highly toxic waste should never have left the Euro…

## 402 · squad · squad:5ad248e0d7d075001a428b85

**原题：** Who concluded that the rising income inequality gap was getting better?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** The reference says widening inequality slowed recovery, not that rising inequality was getting better. Other retrieved researchers discuss different inequality measures.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-eae7a2c5f51128ed8109f0d6` · Economic_inequality：In 2014, economists with the Standard & Poor's rating agency concluded that the widening disparity between the U.S.'s wealthiest citizens and the rest of the nation had slowed its recovery from the 2008-2009 recession and made it more prone to boom-and-bust cy…

## 403 · squad · squad:5ad24b96d7d075001a428bf2

**原题：** What contributed to the increased inequality between trained and untrained workers?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** Original reference describes decreased inequality during education expansion; question asks increased inequality without a period. Other retrieved passages may explain increases, so original empty gold is not global unanswerability.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-3a275c78afdcf71f19504025` · Economic_inequality：During the mass high school education movement from 1910–1940, there was an increase in skilled workers, which led to a decrease in the price of skilled labor. High school education during the period was designed to equip students with necessary skill sets to …

## 404 · squad · squad:5ad25d49d7d075001a428eef

**原题：** In what year did Rene Goulaine de Laudonniere leave his position as a lieutenant for Jean Ribault?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** Being called Ribault’s former lieutenant in 1564 dates a voyage, not when the employment/position ended.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-1b9838fa2d4d2e280b40522d` · Huguenot：French Huguenots made two attempts to establish a haven in North America. In 1562, naval officer Jean Ribault led an expedition that explored Florida and the present-day Southeastern U.S., and founded the outpost of Charlesfort on Parris Island, South Carolina…

## 405 · squad · squad:5ad28098d7d075001a4297b8

**原题：** What the least dominant economic sectors in the Middle Rhine area?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** Reference names dominant sectors, whereas question asks least dominant; no ranked full sector list establishes the answer.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-6d7a3e0599f2269abfd0dfa2` · Rhine：The dominant economic sectors in the Middle Rhine area are viniculture and tourism. The Rhine Gorge between Rüdesheim am Rhein and Koblenz is listed as a UNESCO World Heritage Site. Near Sankt Goarshausen, the Rhine flows around the famous rock Lorelei. With i…

## 406 · squad · squad:5ad3cc47604f3c001a3ff147

**原题：** Along with electric motors, what power sources overtook expansion engines in the 20th century?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** The source discusses replacement of reciprocating/expansion steam engines and shipping’s steam turbines, so the intended answer is recoverable, but “expansion engines” versus all applications leaves scope unresolved.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-b847ff8addf300b6aefff90a` · Steam_engine：Near the end of the 19th century compound engines came into widespread use. Compound engines exhausted steam in to successively larger cylinders to accommodate the higher volumes at reduced pressures, giving improved efficiency. These stages were called expans…

## 407 · squad · squad:5ad3e8f6604f3c001a3ff66e

**原题：** Who developed a successful steam engine for Charles Porter?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** Charles Richard developed an engine indicator for Porter, not the successful engine the question asks for.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-4ca03bcc119071881ff2afa6` · Steam_engine：The most useful instrument for analyzing the performance of steam engines is the steam engine indicator. Early versions were in use by 1851, but the most successful indicator was developed for the high speed engine inventor and manufacturer Charles Porter by C…

## 408 · squad · squad:5ad3f4ed604f3c001a3ff964

**原题：** What cycle has an issue for which an expansion gradient attempts to fix?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** Question says an expansion gradient fixes a cycle, whereas source says uniflow attempts to remedy counterflow cooling and itself has gradient difficulties.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-eafe513291bba1e61901a2ec` · Steam_engine：Uniflow engines attempt to remedy the difficulties arising from the usual counterflow cycle where, during each stroke, the port and the cylinder walls will be cooled by the passing exhaust steam, whilst the hotter incoming admission steam will waste some of it…

## 409 · squad · squad:5ad405f0604f3c001a3ffe31

**原题：** Who took the throne after Kusala's uprising?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** The cited sequence follows Kusala’s death, not an identified uprising; question substitutes a different event.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-8acc2af9234c1f0193c787cb` · Yuan_dynasty：When Yesün Temür died in Shangdu in 1328, Tugh Temür was recalled to Khanbaliq by the Qipchaq commander El Temür. He was installed as the emperor (Emperor Wenzong) in Khanbaliq, while Yesün Temür's son Ragibagh succeeded to the throne in Shangdu with the suppo…

## 410 · squad · squad:5ad40bd9604f3c001a400035

**原题：** What was the Yuan's plastic money called?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** Chao is paper money, not plastic money. The requested material premise is false.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-0186eb12c5f03fee968578c6` · Yuan_dynasty：One of the more notable applications of printing technology was the chao, the paper money of the Yuan. Chao were made from the bark of mulberry trees. The Yuan government used woodblocks to print paper money, but switched to bronze plates in 1275. The Mongols …

## 411 · squad · squad:5ad4f7235b96ef001a10a7de

**原题：** When was did Warsaw's stock exchange decline?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** The passage gives establishment, wartime cessation and reopening, not a stock-market decline; question lacks which decline/time-series metric.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-2a2664f0243872eea0b514c2` · Warsaw：Warsaw's first stock exchange was established in 1817 and continued trading until World War II. It was re-established in April 1991, following the end of the post-war communist control of the country and the reintroduction of a free-market economy. Today, the …

## 412 · squad · squad:5ad5489b5b96ef001a10ac2b

**原题：** What type of machine is a computational model that is not allowed to branch out to check many different possibilities at once?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `false_unanswerable_reference`。

**裁定：** A deterministic Turing machine does not branch as a nondeterministic one does. Empty original gold does not invalidate this correct contrasting computational model.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-62376527a00d3d72a00bdf4d` · Computational_complexity_theory：However, some computational problems are easier to analyze in terms of more unusual resources. For example, a non-deterministic Turing machine is a computational model that is allowed to branch out to check many different possibilities at once. The non-determi…

## 413 · squad · squad:5ad54b035b96ef001a10ac53

**原题：** What are the least well known complexity resources?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** Other named complexity resources are less familiar examples, not a demonstrated set of the least known; no survey or ranking is given.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-d1226941e28b7858588e7ea9` · Computational_complexity_theory：Analogous definitions can be made for space requirements. Although time and space are the most well-known complexity resources, any complexity measure can be viewed as a computational resource. Complexity measures are very generally defined by the Blum complex…

## 414 · squad · squad:5ad560b85b96ef001a10ad21

**原题：** What is least important complexity class of counting problems?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** Reference calls #P important; “least important” is subjective and not supported by a ranking.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-30b50fbcff30406feb1d3d41` · Computational_complexity_theory：Other important complexity classes include BPP, ZPP and RP, which are defined using probabilistic Turing machines; AC and NC, which are defined using Boolean circuits; and BQP and QMA, which are defined using quantum Turing machines. #P is an important complex…

## 415 · squad · squad:5ad5632f5b96ef001a10ad70

**原题：** What are the six types of reductions?

**原金标：** `[]`；原标无答案。

**问题类型：** `original_unanswerable`, `reference_or_scope_unresolved`。

**裁定：** Source gives five examples across two overlapping reduction categories and no canonical list of six; question supplies an unsupported cardinality.

**主表处理：** 所有模型与方案一并排除；原始回答、旧得分、条件性内容裁定保留。

- 原上下文 `s-d0e6843a2b6ff86990a249e4` · Computational_complexity_theory：Many complexity classes are defined using the concept of a reduction. A reduction is a transformation of one problem into another problem. It captures the informal notion of a problem being at least as difficult as another problem. For instance, if a problem X…
