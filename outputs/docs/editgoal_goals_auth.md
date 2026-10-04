# https://yandex.com/dev/metrika/ru/management/openapi/goal/editGoal

# Изменение цели

Изменяет настройки указанной цели счетчика.

## Request

PUT

```
https://api-metrika.yandex.net/management/v1/counter/{counterId}/goal/{goalId}
```

### Path parameters

|  |  |
| --- | --- |
| **Name** | **Description** |
| *counterId* | **Type**: integer  Идентификатор счетчика, цель которого вы хотите изменить. |
| *goalId* | **Type**: integer  Идентификатор цели, настройки которой вы хотите изменить. |

### Body

application/json

```
{
  "goal": {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "is_favorite": true,
    "status": "example",
    "conditions": [
      {}
    ]
  }
}
```

|  |  |
| --- | --- |
| **Name** | **Description** |
| *goal* | **One of 13 types** * **Type**: ActionGoal  Целевое событие. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` * **Type**: ChatGoal  Нажатие на чат. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "is_favorite": true,     "status": "example",     "conditions": [       null     ]   }   ``` * **Type**: CompositeGoal  Составная цель.   Нужна для группировки и задания порядка обычных целей.   В качестве шагов может содержать цели типа "Посещение страниц" и "JavaScript-событие".   Шаг считается достигнутым, если были достигнуты все предыдущие шаги, и после этого были выполнены все условия текущего шага. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "is_favorite": true,     "status": "example",     "steps": [       null     ]   }   ``` * **Type**: DepthGoal  Количество просмотров.   Цель считается достигнутой, если посетитель просмотрел заданное количество страниц сайта. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "is_favorite": true,     "status": "example",     "depth": 2   }   ``` * **Type**: EmailGoal  Нажатие на email. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` * **Type**: FileGoal  Скачивание файлов.   Цель считается достигнутой, если посетитель скачал любой файл или определенный файл. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "is_favorite": true,     "status": "example",     "conditions": [       null     ]   }   ``` * **Type**: MessengerGoal  Переход в мессенджер.   Цель будет достигнута при клике пользователем на ссылку, которая ведет в мессенджер. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` * **Type**: PaymentSystemGoal  Платежные системы.   Цель считается достигнутой, если посетитель совершил оплату через платежную систему. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "is_favorite": true,     "status": "example"   }   ``` * **Type**: PhoneGoal  Нажатие на номер телефона. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ],     "hide_phone_number": true   }   ``` * **Type**: SiteSearchGoal  Поиск по сайту.   Цель будет достигнута при поиске на сайте, если в урле в get-параметрах есть хотя бы одно совпадение. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` * **Type**: SocialNetworkGoal  Переход в социальную сеть.   Цель будет достигнута при клике пользователем на ссылку, которая ведет в социальную сеть. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "is_favorite": true,     "status": "example",     "conditions": [       null     ]   }   ``` * **Type**: UrlGoal  Посещение страниц.   Достигается, когда выполняется хотя бы одно из условий. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` * **Type**: VisitDurationGoal  Продолжительность визита.   Цель будет достигнута при времени визита больше заданного. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "is_favorite": true,     "status": "example",     "duration": 1   }   ``` **Example** ``` {   "id": 0,   "name": "example",   "type": "example",   "default_price": 0.5,   "is_favorite": true,   "status": "example",   "conditions": [     {       "type": "example",       "url": "example"     }   ] } ``` |

### GoalE

Информация о цели.

|  |  |
| --- | --- |
| **Name** | **Description** |
| *type* | **Type**: string  Тип цели:   * `action` — JavaScript-событие. * `chat` — клик по чату. * `email` — клик по email. * `file` — скачивание файлов. * `messenger` — переход в мессенджер. * `number` — количество просмотров. * `payment_system` — платежная система. * `phone` — клик по номеру телефона. * `search` — поиск по сайту. * `social` — переход в соцсети. * `step` — составная цель. * `url` — посещение страниц. * `visit_duration` — продолжительность визита.   *Example:* `example` |
| *default\_price* | **Type**: number  Цена цели по умолчанию. |
| *id* | **Type**: integer  Идентификатор цели. Укажите данный параметр при изменении и удалении цели счетчика. |
| *is\_favorite* | **Type**: boolean  Является ли цель избранной:   * 0 ― не является (по умолчанию). * 1 ― является. |
| *name* | **Type**: string  Наименование цели.  *Min length:* `0`  *Max length:* `255`  *Example:* `example` |
| *status* | **Type**: string  *Example:* `example` |

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "is_favorite": true,
  "status": "example"
}
```

### ActionGoalCondition

Список структур с условиями цели.

|  |  |
| --- | --- |
| **Name** | **Description** |
| *type* | **Type**: string  Тип условия. Возможные значения:   * `contain` — содержит. * `exact` — совпадает. * `start` — начинается с. * `regexp` — удовлетворяет регулярному выражению.   *Example:* `example` |
| *url* | **Type**: string  Значение.  *Min length:* `0`  *Max length:* `16384`  *Example:* `example` |

**Example**

```
{
  "type": "example",
  "url": "example"
}
```

### ActionGoal

Целевое событие.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *conditions* | **Type**: ActionGoalCondition[] **Example** ``` [   {     "type": "example",     "url": "example"   } ] ``` |

  **Example**

  ```
  {
    "conditions": [
      {
        "type": "example",
        "url": "example"
      }
    ]
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "is_favorite": true,
  "status": "example",
  "conditions": [
    {
      "type": "example",
      "url": "example"
    }
  ]
}
```

### ChatGoalCondition

Список структур с условиями цели.

|  |  |
| --- | --- |
| **Name** | **Description** |
| *field* | **Type**: string  К чему применять цель:   * `chat_answered` — статус ответа в чате. * `chat_platform` — платформа чата. * `chat_tag` — тег чата.   *Example:* `example` |

**Example**

```
{
  "field": "example"
}
```

### ChatGoalConditionAnswered

**All of 2 types**

* **Type**: ChatGoalCondition

  Список структур с условиями цели.

  **Example**

  ```
  {
    "field": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *answered* | **Type**: boolean  Статус ответа в чате. |

  **Example**

  ```
  {
    "answered": true
  }
  ```

**Example**

```
{
  "field": "example",
  "answered": true
}
```

### ChatGoalConditionPlatform

**All of 2 types**

* **Type**: ChatGoalCondition

  Список структур с условиями цели.

  **Example**

  ```
  {
    "field": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *platform* | **Type**: string  Платформа чата. Возможные значения:  + `telegram`; + `viber`; + `whatsApp`. *Example:* `telegram` |

  **Example**

  ```
  {
    "platform": "telegram"
  }
  ```

**Example**

```
{
  "field": "example",
  "platform": "telegram"
}
```

### ChatGoalConditionTag

**All of 2 types**

* **Type**: ChatGoalCondition

  Список структур с условиями цели.

  **Example**

  ```
  {
    "field": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *operator* | **Type**: string  Тип условия. Возможные значения:  + `contain` — содержит. + `exact` — совпадает. + `start` — начинается с. + `regexp` — удовлетворяет регулярному выражению. *Example:* `example` |
  | *value* | **Type**: string  *Min length:* `0`  *Max length:* `1024`  *Example:* `example` |

  **Example**

  ```
  {
    "operator": "example",
    "value": "example"
  }
  ```

**Example**

```
{
  "field": "example",
  "operator": "example",
  "value": "example"
}
```

### ChatGoal

Нажатие на чат.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *conditions* | **Type**: array **One of 3 types** + **Type**: ChatGoalConditionAnswered **Example** ```   {     "field": "example",     "answered": true   }   ``` + **Type**: ChatGoalConditionPlatform **Example** ```   {     "field": "example",     "platform": "telegram"   }   ``` + **Type**: ChatGoalConditionTag **Example** ```   {     "field": "example",     "operator": "example",     "value": "example"   }   ``` **Example** ``` [   {     "field": "example",     "answered": true   } ] ``` |

  **Example**

  ```
  {
    "conditions": [
      {
        "field": "example",
        "answered": true
      }
    ]
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "is_favorite": true,
  "status": "example",
  "conditions": [
    null
  ]
}
```

### CompositeGoal

Составная цель.
Нужна для группировки и задания порядка обычных целей.
В качестве шагов может содержать цели типа "Посещение страниц" и "JavaScript-событие".
Шаг считается достигнутым, если были достигнуты все предыдущие шаги, и после этого были выполнены все условия текущего шага.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *steps* | **Type**: array **One of 13 types** + **Type**: ActionGoal  Целевое событие. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` + **Type**: ChatGoal  Нажатие на чат. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "is_favorite": true,     "status": "example",     "conditions": [       null     ]   }   ``` + **Type**: CompositeGoal  Составная цель.   Нужна для группировки и задания порядка обычных целей.   В качестве шагов может содержать цели типа "Посещение страниц" и "JavaScript-событие".   Шаг считается достигнутым, если были достигнуты все предыдущие шаги, и после этого были выполнены все условия текущего шага. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "is_favorite": true,     "status": "example",     "steps": [       null     ]   }   ``` + **Type**: DepthGoal  Количество просмотров.   Цель считается достигнутой, если посетитель просмотрел заданное количество страниц сайта. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "is_favorite": true,     "status": "example",     "depth": 2   }   ``` + **Type**: EmailGoal  Нажатие на email. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` + **Type**: FileGoal  Скачивание файлов.   Цель считается достигнутой, если посетитель скачал любой файл или определенный файл. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "is_favorite": true,     "status": "example",     "conditions": [       null     ]   }   ``` + **Type**: MessengerGoal  Переход в мессенджер.   Цель будет достигнута при клике пользователем на ссылку, которая ведет в мессенджер. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` + **Type**: PaymentSystemGoal  Платежные системы.   Цель считается достигнутой, если посетитель совершил оплату через платежную систему. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "is_favorite": true,     "status": "example"   }   ``` + **Type**: PhoneGoal  Нажатие на номер телефона. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ],     "hide_phone_number": true   }   ``` + **Type**: SiteSearchGoal  Поиск по сайту.   Цель будет достигнута при поиске на сайте, если в урле в get-параметрах есть хотя бы одно совпадение. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` + **Type**: SocialNetworkGoal  Переход в социальную сеть.   Цель будет достигнута при клике пользователем на ссылку, которая ведет в социальную сеть. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "is_favorite": true,     "status": "example",     "conditions": [       null     ]   }   ``` + **Type**: UrlGoal  Посещение страниц.   Достигается, когда выполняется хотя бы одно из условий. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` + **Type**: VisitDurationGoal  Продолжительность визита.   Цель будет достигнута при времени визита больше заданного. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "is_favorite": true,     "status": "example",     "duration": 1   }   ```  *Min items:* `0`  *Max items:* `5`  *Min items:* `0`  *Max items:* `5` **Example** ``` [   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "is_favorite": true,     "status": "example",     "conditions": [       {}     ]   } ] ``` |

  **Example**

  ```
  {
    "steps": [
      {
        "id": 0,
        "name": "example",
        "type": "example",
        "default_price": 0.5,
        "is_favorite": true,
        "status": "example",
        "conditions": [
          null
        ]
      }
    ]
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "is_favorite": true,
  "status": "example",
  "steps": [
    null
  ]
}
```

### DepthGoal

Количество просмотров.
Цель считается достигнутой, если посетитель просмотрел заданное количество страниц сайта.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *depth* | **Type**: integer  Количество просмотренных пользователем страниц.  *Min value:* `2` |

  **Example**

  ```
  {
    "depth": 2
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "is_favorite": true,
  "status": "example",
  "depth": 2
}
```

### EmailGoalCondition

Список структур с условиями цели.

|  |  |
| --- | --- |
| **Name** | **Description** |
| *url* | **Type**: string  Значение.  *Min length:* `0`  *Max length:* `1024`  *Example:* `example` |
| *type* | **Type**: string  *Example:* `example` |

**Example**

```
{
  "type": "example",
  "url": "example"
}
```

### EmailGoal

Нажатие на email.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *conditions* | **Type**: EmailGoalCondition[] **Example** ``` [   {     "type": "example",     "url": "example"   } ] ``` |

  **Example**

  ```
  {
    "conditions": [
      {
        "type": "example",
        "url": "example"
      }
    ]
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "is_favorite": true,
  "status": "example",
  "conditions": [
    {
      "type": "example",
      "url": "example"
    }
  ]
}
```

### FileGoalCondition

Список структур с условиями цели.

|  |  |
| --- | --- |
| **Name** | **Description** |
| *type* | **Type**: string  Тип условия. Возможные значения:   * `all_files` * `file`   *Example:* `example` |

**Example**

```
{
  "type": "example"
}
```

### FileGoalConditionAllFiles

**All of 1 type**

* **Type**: FileGoalCondition

  Список структур с условиями цели.

  **Example**

  ```
  {
    "type": "example"
  }
  ```

**Example**

```
{
  "type": "example"
}
```

### FileGoalConditionFile

**All of 2 types**

* **Type**: FileGoalCondition

  Список структур с условиями цели.

  **Example**

  ```
  {
    "type": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *url* | **Type**: string  Значение.  *Min length:* `0`  *Max length:* `16384`  *Example:* `example` |

  **Example**

  ```
  {
    "url": "example"
  }
  ```

**Example**

```
{
  "type": "example",
  "url": "example"
}
```

### FileGoal

Скачивание файлов.
Цель считается достигнутой, если посетитель скачал любой файл или определенный файл.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *conditions* | **Type**: array **One of 2 types** + **Type**: FileGoalConditionAllFiles **Example** ```   {     "type": "example"   }   ``` + **Type**: FileGoalConditionFile **Example** ```   {     "type": "example",     "url": "example"   }   ``` **Example** ``` [   {     "type": "example"   } ] ``` |

  **Example**

  ```
  {
    "conditions": [
      {
        "type": "example"
      }
    ]
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "is_favorite": true,
  "status": "example",
  "conditions": [
    null
  ]
}
```

### MessengerGoalCondition

Список структур с условиями цели.

|  |  |
| --- | --- |
| **Name** | **Description** |
| *url* | **Type**: string  Значение.  *Min length:* `0`  *Max length:* `16384`  *Example:* `example` |
| *type* | **Type**: string  *Example:* `example` |

**Example**

```
{
  "type": "example",
  "url": "example"
}
```

### MessengerGoal

Переход в мессенджер.
Цель будет достигнута при клике пользователем на ссылку, которая ведет в мессенджер.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *conditions* | **Type**: MessengerGoalCondition[] **Example** ``` [   {     "type": "example",     "url": "example"   } ] ``` |

  **Example**

  ```
  {
    "conditions": [
      {
        "type": "example",
        "url": "example"
      }
    ]
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "is_favorite": true,
  "status": "example",
  "conditions": [
    {
      "type": "example",
      "url": "example"
    }
  ]
}
```

### PaymentSystemGoal

Платежные системы.
Цель считается достигнутой, если посетитель совершил оплату через платежную систему.

**All of 1 type**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "is_favorite": true,
    "status": "example"
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "is_favorite": true,
  "status": "example"
}
```

### PhoneGoalCondition

Список структур с условиями цели.

|  |  |
| --- | --- |
| **Name** | **Description** |
| *url* | **Type**: string  Значение.  *Min length:* `0`  *Max length:* `25`  *Example:* `example` |
| *type* | **Type**: string  *Example:* `example` |

**Example**

```
{
  "type": "example",
  "url": "example"
}
```

### PhoneGoal

Нажатие на номер телефона.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *conditions* | **Type**: PhoneGoalCondition[] **Example** ``` [   {     "type": "example",     "url": "example"   } ] ``` |
  | *hide\_phone\_number* | **Type**: boolean  Скрывать номер телефона на десктопах. |

  **Example**

  ```
  {
    "conditions": [
      {
        "type": "example",
        "url": "example"
      }
    ],
    "hide_phone_number": true
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "is_favorite": true,
  "status": "example",
  "conditions": [
    {
      "type": "example",
      "url": "example"
    }
  ],
  "hide_phone_number": true
}
```

### SiteSearchGoal

Поиск по сайту.
Цель будет достигнута при поиске на сайте, если в урле в get-параметрах есть хотя бы одно совпадение.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *conditions* | **Type**: SiteSearchGoalCondition[] **Example** ``` [   {     "type": "example",     "url": "example"   } ] ``` |

  **Example**

  ```
  {
    "conditions": [
      {
        "type": "example",
        "url": "example"
      }
    ]
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "is_favorite": true,
  "status": "example",
  "conditions": [
    {
      "type": "example",
      "url": "example"
    }
  ]
}
```

### SocialNetworkGoalCondition

Список структур с условиями цели.

|  |  |
| --- | --- |
| **Name** | **Description** |
| *type* | **Type**: string  Тип условия. Возможные значения:   * `all_social` * `social`   *Example:* `example` |

**Example**

```
{
  "type": "example"
}
```

### SocialNetworkGoalConditionAllSocial

**All of 1 type**

* **Type**: SocialNetworkGoalCondition

  Список структур с условиями цели.

  **Example**

  ```
  {
    "type": "example"
  }
  ```

**Example**

```
{
  "type": "example"
}
```

### SocialNetworkGoalConditionSocial

**All of 2 types**

* **Type**: SocialNetworkGoalCondition

  Список структур с условиями цели.

  **Example**

  ```
  {
    "type": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *url* | **Type**: string  Значение.  *Min length:* `0`  *Max length:* `16384`  *Example:* `example` |

  **Example**

  ```
  {
    "url": "example"
  }
  ```

**Example**

```
{
  "type": "example",
  "url": "example"
}
```

### SocialNetworkGoal

Переход в социальную сеть.
Цель будет достигнута при клике пользователем на ссылку, которая ведет в социальную сеть.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *conditions* | **Type**: array **One of 2 types** + **Type**: SocialNetworkGoalConditionAllSocial **Example** ```   {     "type": "example"   }   ``` + **Type**: SocialNetworkGoalConditionSocial **Example** ```   {     "type": "example",     "url": "example"   }   ``` **Example** ``` [   {     "type": "example"   } ] ``` |

  **Example**

  ```
  {
    "conditions": [
      {
        "type": "example"
      }
    ]
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "is_favorite": true,
  "status": "example",
  "conditions": [
    null
  ]
}
```

### UrlGoalCondition

Список структур с условиями цели.

|  |  |
| --- | --- |
| **Name** | **Description** |
| *type* | **Type**: string  Тип условия. Возможные значения:   * `contain` — содержит. * `exact` — совпадает. * `start` — начинается с. * `regexp` — удовлетворяет регулярному выражению. * `action` — используется в составных целях. * `regexp_action` — используется в составных целях. * `contain_action` — используется в составных целях.   *Example:* `example` |
| *url* | **Type**: string  Значение.  *Min length:* `0`  *Max length:* `16384`  *Example:* `example` |

**Example**

```
{
  "type": "example",
  "url": "example"
}
```

### UrlGoal

Посещение страниц.
Достигается, когда выполняется хотя бы одно из условий.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *conditions* | **Type**: UrlGoalCondition[] **Example** ``` [   {     "type": "example",     "url": "example"   } ] ``` |

  **Example**

  ```
  {
    "conditions": [
      {
        "type": "example",
        "url": "example"
      }
    ]
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "is_favorite": true,
  "status": "example",
  "conditions": [
    {
      "type": "example",
      "url": "example"
    }
  ]
}
```

### VisitDurationGoal

Продолжительность визита.
Цель будет достигнута при времени визита больше заданного.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *duration* | **Type**: integer  Продолжительность визита в секундах.  *Min value:* `1` |

  **Example**

  ```
  {
    "duration": 1
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "is_favorite": true,
  "status": "example",
  "duration": 1
}
```

## Responses

## 200 OK

OK

### Body

application/json

```
{
  "goal": {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example",
    "conditions": [
      {}
    ]
  }
}
```

|  |  |
| --- | --- |
| **Name** | **Description** |
| *goal* | **One of 13 types** * **Type**: ActionGoal  Целевое событие. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` * **Type**: ChatGoal  Нажатие на чат. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       null     ]   }   ``` * **Type**: CompositeGoal  Составная цель.   Нужна для группировки и задания порядка обычных целей.   В качестве шагов может содержать цели типа "Посещение страниц" и "JavaScript-событие".   Шаг считается достигнутым, если были достигнуты все предыдущие шаги, и после этого были выполнены все условия текущего шага. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "steps": [       null     ]   }   ``` * **Type**: DepthGoal  Количество просмотров.   Цель считается достигнутой, если посетитель просмотрел заданное количество страниц сайта. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "depth": 2   }   ``` * **Type**: EmailGoal  Нажатие на email. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` * **Type**: FileGoal  Скачивание файлов.   Цель считается достигнутой, если посетитель скачал любой файл или определенный файл. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       null     ]   }   ``` * **Type**: MessengerGoal  Переход в мессенджер.   Цель будет достигнута при клике пользователем на ссылку, которая ведет в мессенджер. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` * **Type**: PaymentSystemGoal  Платежные системы.   Цель считается достигнутой, если посетитель совершил оплату через платежную систему. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example"   }   ``` * **Type**: PhoneGoal  Нажатие на номер телефона. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ],     "hide_phone_number": true   }   ``` * **Type**: SiteSearchGoal  Поиск по сайту.   Цель будет достигнута при поиске на сайте, если в урле в get-параметрах есть хотя бы одно совпадение. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` * **Type**: SocialNetworkGoal  Переход в социальную сеть.   Цель будет достигнута при клике пользователем на ссылку, которая ведет в социальную сеть. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       null     ]   }   ``` * **Type**: UrlGoal  Посещение страниц.   Достигается, когда выполняется хотя бы одно из условий. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` * **Type**: VisitDurationGoal  Продолжительность визита.   Цель будет достигнута при времени визита больше заданного. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "duration": 1   }   ``` **Example** ``` {   "id": 0,   "name": "example",   "type": "example",   "default_price": 0.5,   "goal_source": "example",   "is_favorite": true,   "status": "example",   "conditions": [     {       "type": "example",       "url": "example"     }   ] } ``` |

### GoalE

Информация о цели.

|  |  |
| --- | --- |
| **Name** | **Description** |
| *type* | **Type**: string  Тип цели:   * `action` — JavaScript-событие. * `chat` — клик по чату. * `email` — клик по email. * `file` — скачивание файлов. * `messenger` — переход в мессенджер. * `number` — количество просмотров. * `payment_system` — платежная система. * `phone` — клик по номеру телефона. * `search` — поиск по сайту. * `social` — переход в соцсети. * `step` — составная цель. * `url` — посещение страниц. * `visit_duration` — продолжительность визита.   *Example:* `example` |
| *default\_price* | **Type**: number  Цена цели по умолчанию. |
| *goal\_source* | **Type**: string  Признак того, как создана цель:   * `user` — цель создана пользователем Метрики. * `auto` — цель создана автоматически. К таким целям относятся т. н. автоматические цели, цель **Звонок** (создается при передаче данных о звонках).   *Example:* `example` |
| *id* | **Type**: integer  Идентификатор цели. Укажите данный параметр при изменении и удалении цели счетчика. |
| *is\_favorite* | **Type**: boolean  Является ли цель избранной:   * 0 ― не является (по умолчанию). * 1 ― является. |
| *name* | **Type**: string  Наименование цели.  *Min length:* `0`  *Max length:* `255`  *Example:* `example` |
| *status* | **Type**: string  *Example:* `example` |

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example"
}
```

### ActionGoal

Целевое событие.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *conditions* | **Type**: ActionGoalCondition[] **Example** ``` [   {     "type": "example",     "url": "example"   } ] ``` |

  **Example**

  ```
  {
    "conditions": [
      {
        "type": "example",
        "url": "example"
      }
    ]
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example",
  "conditions": [
    {
      "type": "example",
      "url": "example"
    }
  ]
}
```

### ChatGoal

Нажатие на чат.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *conditions* | **Type**: array **One of 3 types** + **Type**: ChatGoalConditionAnswered **Example** ```   {     "field": "example",     "answered": true   }   ``` + **Type**: ChatGoalConditionPlatform **Example** ```   {     "field": "example",     "platform": "telegram"   }   ``` + **Type**: ChatGoalConditionTag **Example** ```   {     "field": "example",     "operator": "example",     "value": "example"   }   ``` **Example** ``` [   {     "field": "example",     "answered": true   } ] ``` |

  **Example**

  ```
  {
    "conditions": [
      {
        "field": "example",
        "answered": true
      }
    ]
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example",
  "conditions": [
    null
  ]
}
```

### CompositeGoal

Составная цель.
Нужна для группировки и задания порядка обычных целей.
В качестве шагов может содержать цели типа "Посещение страниц" и "JavaScript-событие".
Шаг считается достигнутым, если были достигнуты все предыдущие шаги, и после этого были выполнены все условия текущего шага.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *steps* | **Type**: array **One of 13 types** + **Type**: ActionGoal  Целевое событие. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` + **Type**: ChatGoal  Нажатие на чат. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       null     ]   }   ``` + **Type**: CompositeGoal  Составная цель.   Нужна для группировки и задания порядка обычных целей.   В качестве шагов может содержать цели типа "Посещение страниц" и "JavaScript-событие".   Шаг считается достигнутым, если были достигнуты все предыдущие шаги, и после этого были выполнены все условия текущего шага. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "steps": [       null     ]   }   ``` + **Type**: DepthGoal  Количество просмотров.   Цель считается достигнутой, если посетитель просмотрел заданное количество страниц сайта. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "depth": 2   }   ``` + **Type**: EmailGoal  Нажатие на email. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` + **Type**: FileGoal  Скачивание файлов.   Цель считается достигнутой, если посетитель скачал любой файл или определенный файл. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       null     ]   }   ``` + **Type**: MessengerGoal  Переход в мессенджер.   Цель будет достигнута при клике пользователем на ссылку, которая ведет в мессенджер. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` + **Type**: PaymentSystemGoal  Платежные системы.   Цель считается достигнутой, если посетитель совершил оплату через платежную систему. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example"   }   ``` + **Type**: PhoneGoal  Нажатие на номер телефона. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ],     "hide_phone_number": true   }   ``` + **Type**: SiteSearchGoal  Поиск по сайту.   Цель будет достигнута при поиске на сайте, если в урле в get-параметрах есть хотя бы одно совпадение. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` + **Type**: SocialNetworkGoal  Переход в социальную сеть.   Цель будет достигнута при клике пользователем на ссылку, которая ведет в социальную сеть. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       null     ]   }   ``` + **Type**: UrlGoal  Посещение страниц.   Достигается, когда выполняется хотя бы одно из условий. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` + **Type**: VisitDurationGoal  Продолжительность визита.   Цель будет достигнута при времени визита больше заданного. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "duration": 1   }   ```  *Min items:* `0`  *Max items:* `5`  *Min items:* `0`  *Max items:* `5` **Example** ``` [   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {}     ]   } ] ``` |

  **Example**

  ```
  {
    "steps": [
      {
        "id": 0,
        "name": "example",
        "type": "example",
        "default_price": 0.5,
        "goal_source": "example",
        "is_favorite": true,
        "status": "example",
        "conditions": [
          null
        ]
      }
    ]
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example",
  "steps": [
    null
  ]
}
```

### DepthGoal

Количество просмотров.
Цель считается достигнутой, если посетитель просмотрел заданное количество страниц сайта.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *depth* | **Type**: integer  Количество просмотренных пользователем страниц.  *Min value:* `2` |

  **Example**

  ```
  {
    "depth": 2
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example",
  "depth": 2
}
```

### EmailGoal

Нажатие на email.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *conditions* | **Type**: EmailGoalCondition[] **Example** ``` [   {     "type": "example",     "url": "example"   } ] ``` |

  **Example**

  ```
  {
    "conditions": [
      {
        "type": "example",
        "url": "example"
      }
    ]
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example",
  "conditions": [
    {
      "type": "example",
      "url": "example"
    }
  ]
}
```

### FileGoal

Скачивание файлов.
Цель считается достигнутой, если посетитель скачал любой файл или определенный файл.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *conditions* | **Type**: array **One of 2 types** + **Type**: FileGoalConditionAllFiles **Example** ```   {     "type": "example"   }   ``` + **Type**: FileGoalConditionFile **Example** ```   {     "type": "example",     "url": "example"   }   ``` **Example** ``` [   {     "type": "example"   } ] ``` |

  **Example**

  ```
  {
    "conditions": [
      {
        "type": "example"
      }
    ]
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example",
  "conditions": [
    null
  ]
}
```

### MessengerGoal

Переход в мессенджер.
Цель будет достигнута при клике пользователем на ссылку, которая ведет в мессенджер.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *conditions* | **Type**: MessengerGoalCondition[] **Example** ``` [   {     "type": "example",     "url": "example"   } ] ``` |

  **Example**

  ```
  {
    "conditions": [
      {
        "type": "example",
        "url": "example"
      }
    ]
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example",
  "conditions": [
    {
      "type": "example",
      "url": "example"
    }
  ]
}
```

### PaymentSystemGoal

Платежные системы.
Цель считается достигнутой, если посетитель совершил оплату через платежную систему.

**All of 1 type**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example"
}
```

### PhoneGoal

Нажатие на номер телефона.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *conditions* | **Type**: PhoneGoalCondition[] **Example** ``` [   {     "type": "example",     "url": "example"   } ] ``` |
  | *hide\_phone\_number* | **Type**: boolean  Скрывать номер телефона на десктопах. |

  **Example**

  ```
  {
    "conditions": [
      {
        "type": "example",
        "url": "example"
      }
    ],
    "hide_phone_number": true
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example",
  "conditions": [
    {
      "type": "example",
      "url": "example"
    }
  ],
  "hide_phone_number": true
}
```

### SiteSearchGoal

Поиск по сайту.
Цель будет достигнута при поиске на сайте, если в урле в get-параметрах есть хотя бы одно совпадение.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *conditions* | **Type**: SiteSearchGoalCondition[] **Example** ``` [   {     "type": "example",     "url": "example"   } ] ``` |

  **Example**

  ```
  {
    "conditions": [
      {
        "type": "example",
        "url": "example"
      }
    ]
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example",
  "conditions": [
    {
      "type": "example",
      "url": "example"
    }
  ]
}
```

### SocialNetworkGoal

Переход в социальную сеть.
Цель будет достигнута при клике пользователем на ссылку, которая ведет в социальную сеть.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *conditions* | **Type**: array **One of 2 types** + **Type**: SocialNetworkGoalConditionAllSocial **Example** ```   {     "type": "example"   }   ``` + **Type**: SocialNetworkGoalConditionSocial **Example** ```   {     "type": "example",     "url": "example"   }   ``` **Example** ``` [   {     "type": "example"   } ] ``` |

  **Example**

  ```
  {
    "conditions": [
      {
        "type": "example"
      }
    ]
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example",
  "conditions": [
    null
  ]
}
```

### UrlGoal

Посещение страниц.
Достигается, когда выполняется хотя бы одно из условий.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *conditions* | **Type**: UrlGoalCondition[] **Example** ``` [   {     "type": "example",     "url": "example"   } ] ``` |

  **Example**

  ```
  {
    "conditions": [
      {
        "type": "example",
        "url": "example"
      }
    ]
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example",
  "conditions": [
    {
      "type": "example",
      "url": "example"
    }
  ]
}
```

### VisitDurationGoal

Продолжительность визита.
Цель будет достигнута при времени визита больше заданного.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *duration* | **Type**: integer  Продолжительность визита в секундах.  *Min value:* `1` |

  **Example**

  ```
  {
    "duration": 1
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example",
  "duration": 1
}
```

Предыдущая

Информация о цели

Следующая

Удаление цели

# https://yandex.com/dev/metrika/ru/management/openapi/goal/goals

# Список целей

Возвращает информацию о целях счетчика.

## Request

GET

```
https://api-metrika.yandex.net/management/v1/counter/{counterId}/goals
```

### Path parameters

|  |  |
| --- | --- |
| **Name** | **Description** |
| *counterId* | **Type**: integer  Идентификатор счетчика, список целей которого вы хотите получить. |

### Query parameters

|  |  |
| --- | --- |
| **Name** | **Description** |
| *callback* | **Type**: string  Функция обратного вызова, которая обрабатывает ответ API.  *Example:* `` |
| *useDeleted* | **Type**: boolean  Информация об удаленных целях.  *Default:* `false` |

## Responses

## 200 OK

OK

### Body

application/json

```
{
  "goals": [
    {
      "id": 0,
      "name": "example",
      "type": "example",
      "default_price": 0.5,
      "goal_source": "example",
      "is_favorite": true,
      "status": "example",
      "conditions": [
        null
      ]
    }
  ]
}
```

|  |  |
| --- | --- |
| **Name** | **Description** |
| *goals* | **Type**: array **One of 13 types** * **Type**: ActionGoal  Целевое событие. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` * **Type**: ChatGoal  Нажатие на чат. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       null     ]   }   ``` * **Type**: CompositeGoal  Составная цель.   Нужна для группировки и задания порядка обычных целей.   В качестве шагов может содержать цели типа "Посещение страниц" и "JavaScript-событие".   Шаг считается достигнутым, если были достигнуты все предыдущие шаги, и после этого были выполнены все условия текущего шага. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "steps": [       null     ]   }   ``` * **Type**: DepthGoal  Количество просмотров.   Цель считается достигнутой, если посетитель просмотрел заданное количество страниц сайта. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "depth": 2   }   ``` * **Type**: EmailGoal  Нажатие на email. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` * **Type**: FileGoal  Скачивание файлов.   Цель считается достигнутой, если посетитель скачал любой файл или определенный файл. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       null     ]   }   ``` * **Type**: MessengerGoal  Переход в мессенджер.   Цель будет достигнута при клике пользователем на ссылку, которая ведет в мессенджер. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` * **Type**: PaymentSystemGoal  Платежные системы.   Цель считается достигнутой, если посетитель совершил оплату через платежную систему. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example"   }   ``` * **Type**: PhoneGoal  Нажатие на номер телефона. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ],     "hide_phone_number": true   }   ``` * **Type**: SiteSearchGoal  Поиск по сайту.   Цель будет достигнута при поиске на сайте, если в урле в get-параметрах есть хотя бы одно совпадение. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` * **Type**: SocialNetworkGoal  Переход в социальную сеть.   Цель будет достигнута при клике пользователем на ссылку, которая ведет в социальную сеть. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       null     ]   }   ``` * **Type**: UrlGoal  Посещение страниц.   Достигается, когда выполняется хотя бы одно из условий. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` * **Type**: VisitDurationGoal  Продолжительность визита.   Цель будет достигнута при времени визита больше заданного. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "duration": 1   }   ```  Список структур с информацией о целях счетчика. **Example** ``` [   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {}     ]   } ] ``` |

### GoalE

Информация о цели.

|  |  |
| --- | --- |
| **Name** | **Description** |
| *type* | **Type**: string  Тип цели:   * `action` — JavaScript-событие. * `chat` — клик по чату. * `email` — клик по email. * `file` — скачивание файлов. * `messenger` — переход в мессенджер. * `number` — количество просмотров. * `payment_system` — платежная система. * `phone` — клик по номеру телефона. * `search` — поиск по сайту. * `social` — переход в соцсети. * `step` — составная цель. * `url` — посещение страниц. * `visit_duration` — продолжительность визита.   *Example:* `example` |
| *default\_price* | **Type**: number  Цена цели по умолчанию. |
| *goal\_source* | **Type**: string  Признак того, как создана цель:   * `user` — цель создана пользователем Метрики. * `auto` — цель создана автоматически. К таким целям относятся т. н. автоматические цели, цель **Звонок** (создается при передаче данных о звонках).   *Example:* `example` |
| *id* | **Type**: integer  Идентификатор цели. Укажите данный параметр при изменении и удалении цели счетчика. |
| *is\_favorite* | **Type**: boolean  Является ли цель избранной:   * 0 ― не является (по умолчанию). * 1 ― является. |
| *name* | **Type**: string  Наименование цели.  *Min length:* `0`  *Max length:* `255`  *Example:* `example` |
| *status* | **Type**: string  *Example:* `example` |

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example"
}
```

### ActionGoalCondition

Список структур с условиями цели.

|  |  |
| --- | --- |
| **Name** | **Description** |
| *type* | **Type**: string  Тип условия. Возможные значения:   * `contain` — содержит. * `exact` — совпадает. * `start` — начинается с. * `regexp` — удовлетворяет регулярному выражению.   *Example:* `example` |
| *url* | **Type**: string  Значение.  *Min length:* `0`  *Max length:* `16384`  *Example:* `example` |

**Example**

```
{
  "type": "example",
  "url": "example"
}
```

### ActionGoal

Целевое событие.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *conditions* | **Type**: ActionGoalCondition[] **Example** ``` [   {     "type": "example",     "url": "example"   } ] ``` |

  **Example**

  ```
  {
    "conditions": [
      {
        "type": "example",
        "url": "example"
      }
    ]
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example",
  "conditions": [
    {
      "type": "example",
      "url": "example"
    }
  ]
}
```

### ChatGoalCondition

Список структур с условиями цели.

|  |  |
| --- | --- |
| **Name** | **Description** |
| *field* | **Type**: string  К чему применять цель:   * `chat_answered` — статус ответа в чате. * `chat_platform` — платформа чата. * `chat_tag` — тег чата.   *Example:* `example` |

**Example**

```
{
  "field": "example"
}
```

### ChatGoalConditionAnswered

**All of 2 types**

* **Type**: ChatGoalCondition

  Список структур с условиями цели.

  **Example**

  ```
  {
    "field": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *answered* | **Type**: boolean  Статус ответа в чате. |

  **Example**

  ```
  {
    "answered": true
  }
  ```

**Example**

```
{
  "field": "example",
  "answered": true
}
```

### ChatGoalConditionPlatform

**All of 2 types**

* **Type**: ChatGoalCondition

  Список структур с условиями цели.

  **Example**

  ```
  {
    "field": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *platform* | **Type**: string  Платформа чата. Возможные значения:  + `telegram`; + `viber`; + `whatsApp`. *Example:* `telegram` |

  **Example**

  ```
  {
    "platform": "telegram"
  }
  ```

**Example**

```
{
  "field": "example",
  "platform": "telegram"
}
```

### ChatGoalConditionTag

**All of 2 types**

* **Type**: ChatGoalCondition

  Список структур с условиями цели.

  **Example**

  ```
  {
    "field": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *operator* | **Type**: string  Тип условия. Возможные значения:  + `contain` — содержит. + `exact` — совпадает. + `start` — начинается с. + `regexp` — удовлетворяет регулярному выражению. *Example:* `example` |
  | *value* | **Type**: string  *Min length:* `0`  *Max length:* `1024`  *Example:* `example` |

  **Example**

  ```
  {
    "operator": "example",
    "value": "example"
  }
  ```

**Example**

```
{
  "field": "example",
  "operator": "example",
  "value": "example"
}
```

### ChatGoal

Нажатие на чат.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *conditions* | **Type**: array **One of 3 types** + **Type**: ChatGoalConditionAnswered **Example** ```   {     "field": "example",     "answered": true   }   ``` + **Type**: ChatGoalConditionPlatform **Example** ```   {     "field": "example",     "platform": "telegram"   }   ``` + **Type**: ChatGoalConditionTag **Example** ```   {     "field": "example",     "operator": "example",     "value": "example"   }   ``` **Example** ``` [   {     "field": "example",     "answered": true   } ] ``` |

  **Example**

  ```
  {
    "conditions": [
      {
        "field": "example",
        "answered": true
      }
    ]
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example",
  "conditions": [
    null
  ]
}
```

### CompositeGoal

Составная цель.
Нужна для группировки и задания порядка обычных целей.
В качестве шагов может содержать цели типа "Посещение страниц" и "JavaScript-событие".
Шаг считается достигнутым, если были достигнуты все предыдущие шаги, и после этого были выполнены все условия текущего шага.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *steps* | **Type**: array **One of 13 types** + **Type**: ActionGoal  Целевое событие. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` + **Type**: ChatGoal  Нажатие на чат. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       null     ]   }   ``` + **Type**: CompositeGoal  Составная цель.   Нужна для группировки и задания порядка обычных целей.   В качестве шагов может содержать цели типа "Посещение страниц" и "JavaScript-событие".   Шаг считается достигнутым, если были достигнуты все предыдущие шаги, и после этого были выполнены все условия текущего шага. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "steps": [       null     ]   }   ``` + **Type**: DepthGoal  Количество просмотров.   Цель считается достигнутой, если посетитель просмотрел заданное количество страниц сайта. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "depth": 2   }   ``` + **Type**: EmailGoal  Нажатие на email. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` + **Type**: FileGoal  Скачивание файлов.   Цель считается достигнутой, если посетитель скачал любой файл или определенный файл. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       null     ]   }   ``` + **Type**: MessengerGoal  Переход в мессенджер.   Цель будет достигнута при клике пользователем на ссылку, которая ведет в мессенджер. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` + **Type**: PaymentSystemGoal  Платежные системы.   Цель считается достигнутой, если посетитель совершил оплату через платежную систему. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example"   }   ``` + **Type**: PhoneGoal  Нажатие на номер телефона. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ],     "hide_phone_number": true   }   ``` + **Type**: SiteSearchGoal  Поиск по сайту.   Цель будет достигнута при поиске на сайте, если в урле в get-параметрах есть хотя бы одно совпадение. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` + **Type**: SocialNetworkGoal  Переход в социальную сеть.   Цель будет достигнута при клике пользователем на ссылку, которая ведет в социальную сеть. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       null     ]   }   ``` + **Type**: UrlGoal  Посещение страниц.   Достигается, когда выполняется хотя бы одно из условий. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` + **Type**: VisitDurationGoal  Продолжительность визита.   Цель будет достигнута при времени визита больше заданного. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "duration": 1   }   ```  *Min items:* `0`  *Max items:* `5`  *Min items:* `0`  *Max items:* `5` **Example** ``` [   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {}     ]   } ] ``` |

  **Example**

  ```
  {
    "steps": [
      {
        "id": 0,
        "name": "example",
        "type": "example",
        "default_price": 0.5,
        "goal_source": "example",
        "is_favorite": true,
        "status": "example",
        "conditions": [
          null
        ]
      }
    ]
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example",
  "steps": [
    null
  ]
}
```

### DepthGoal

Количество просмотров.
Цель считается достигнутой, если посетитель просмотрел заданное количество страниц сайта.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *depth* | **Type**: integer  Количество просмотренных пользователем страниц.  *Min value:* `2` |

  **Example**

  ```
  {
    "depth": 2
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example",
  "depth": 2
}
```

### EmailGoalCondition

Список структур с условиями цели.

|  |  |
| --- | --- |
| **Name** | **Description** |
| *url* | **Type**: string  Значение.  *Min length:* `0`  *Max length:* `1024`  *Example:* `example` |
| *type* | **Type**: string  *Example:* `example` |

**Example**

```
{
  "type": "example",
  "url": "example"
}
```

### EmailGoal

Нажатие на email.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *conditions* | **Type**: EmailGoalCondition[] **Example** ``` [   {     "type": "example",     "url": "example"   } ] ``` |

  **Example**

  ```
  {
    "conditions": [
      {
        "type": "example",
        "url": "example"
      }
    ]
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example",
  "conditions": [
    {
      "type": "example",
      "url": "example"
    }
  ]
}
```

### FileGoalCondition

Список структур с условиями цели.

|  |  |
| --- | --- |
| **Name** | **Description** |
| *type* | **Type**: string  Тип условия. Возможные значения:   * `all_files` * `file`   *Example:* `example` |

**Example**

```
{
  "type": "example"
}
```

### FileGoalConditionAllFiles

**All of 1 type**

* **Type**: FileGoalCondition

  Список структур с условиями цели.

  **Example**

  ```
  {
    "type": "example"
  }
  ```

**Example**

```
{
  "type": "example"
}
```

### FileGoalConditionFile

**All of 2 types**

* **Type**: FileGoalCondition

  Список структур с условиями цели.

  **Example**

  ```
  {
    "type": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *url* | **Type**: string  Значение.  *Min length:* `0`  *Max length:* `16384`  *Example:* `example` |

  **Example**

  ```
  {
    "url": "example"
  }
  ```

**Example**

```
{
  "type": "example",
  "url": "example"
}
```

### FileGoal

Скачивание файлов.
Цель считается достигнутой, если посетитель скачал любой файл или определенный файл.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *conditions* | **Type**: array **One of 2 types** + **Type**: FileGoalConditionAllFiles **Example** ```   {     "type": "example"   }   ``` + **Type**: FileGoalConditionFile **Example** ```   {     "type": "example",     "url": "example"   }   ``` **Example** ``` [   {     "type": "example"   } ] ``` |

  **Example**

  ```
  {
    "conditions": [
      {
        "type": "example"
      }
    ]
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example",
  "conditions": [
    null
  ]
}
```

### MessengerGoalCondition

Список структур с условиями цели.

|  |  |
| --- | --- |
| **Name** | **Description** |
| *url* | **Type**: string  Значение.  *Min length:* `0`  *Max length:* `16384`  *Example:* `example` |
| *type* | **Type**: string  *Example:* `example` |

**Example**

```
{
  "type": "example",
  "url": "example"
}
```

### MessengerGoal

Переход в мессенджер.
Цель будет достигнута при клике пользователем на ссылку, которая ведет в мессенджер.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *conditions* | **Type**: MessengerGoalCondition[] **Example** ``` [   {     "type": "example",     "url": "example"   } ] ``` |

  **Example**

  ```
  {
    "conditions": [
      {
        "type": "example",
        "url": "example"
      }
    ]
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example",
  "conditions": [
    {
      "type": "example",
      "url": "example"
    }
  ]
}
```

### PaymentSystemGoal

Платежные системы.
Цель считается достигнутой, если посетитель совершил оплату через платежную систему.

**All of 1 type**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example"
}
```

### PhoneGoalCondition

Список структур с условиями цели.

|  |  |
| --- | --- |
| **Name** | **Description** |
| *url* | **Type**: string  Значение.  *Min length:* `0`  *Max length:* `25`  *Example:* `example` |
| *type* | **Type**: string  *Example:* `example` |

**Example**

```
{
  "type": "example",
  "url": "example"
}
```

### PhoneGoal

Нажатие на номер телефона.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *conditions* | **Type**: PhoneGoalCondition[] **Example** ``` [   {     "type": "example",     "url": "example"   } ] ``` |
  | *hide\_phone\_number* | **Type**: boolean  Скрывать номер телефона на десктопах. |

  **Example**

  ```
  {
    "conditions": [
      {
        "type": "example",
        "url": "example"
      }
    ],
    "hide_phone_number": true
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example",
  "conditions": [
    {
      "type": "example",
      "url": "example"
    }
  ],
  "hide_phone_number": true
}
```

### SiteSearchGoal

Поиск по сайту.
Цель будет достигнута при поиске на сайте, если в урле в get-параметрах есть хотя бы одно совпадение.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *conditions* | **Type**: SiteSearchGoalCondition[] **Example** ``` [   {     "type": "example",     "url": "example"   } ] ``` |

  **Example**

  ```
  {
    "conditions": [
      {
        "type": "example",
        "url": "example"
      }
    ]
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example",
  "conditions": [
    {
      "type": "example",
      "url": "example"
    }
  ]
}
```

### SocialNetworkGoalCondition

Список структур с условиями цели.

|  |  |
| --- | --- |
| **Name** | **Description** |
| *type* | **Type**: string  Тип условия. Возможные значения:   * `all_social` * `social`   *Example:* `example` |

**Example**

```
{
  "type": "example"
}
```

### SocialNetworkGoalConditionAllSocial

**All of 1 type**

* **Type**: SocialNetworkGoalCondition

  Список структур с условиями цели.

  **Example**

  ```
  {
    "type": "example"
  }
  ```

**Example**

```
{
  "type": "example"
}
```

### SocialNetworkGoalConditionSocial

**All of 2 types**

* **Type**: SocialNetworkGoalCondition

  Список структур с условиями цели.

  **Example**

  ```
  {
    "type": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *url* | **Type**: string  Значение.  *Min length:* `0`  *Max length:* `16384`  *Example:* `example` |

  **Example**

  ```
  {
    "url": "example"
  }
  ```

**Example**

```
{
  "type": "example",
  "url": "example"
}
```

### SocialNetworkGoal

Переход в социальную сеть.
Цель будет достигнута при клике пользователем на ссылку, которая ведет в социальную сеть.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *conditions* | **Type**: array **One of 2 types** + **Type**: SocialNetworkGoalConditionAllSocial **Example** ```   {     "type": "example"   }   ``` + **Type**: SocialNetworkGoalConditionSocial **Example** ```   {     "type": "example",     "url": "example"   }   ``` **Example** ``` [   {     "type": "example"   } ] ``` |

  **Example**

  ```
  {
    "conditions": [
      {
        "type": "example"
      }
    ]
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example",
  "conditions": [
    null
  ]
}
```

### UrlGoalCondition

Список структур с условиями цели.

|  |  |
| --- | --- |
| **Name** | **Description** |
| *type* | **Type**: string  Тип условия. Возможные значения:   * `contain` — содержит. * `exact` — совпадает. * `start` — начинается с. * `regexp` — удовлетворяет регулярному выражению. * `action` — используется в составных целях. * `regexp_action` — используется в составных целях. * `contain_action` — используется в составных целях.   *Example:* `example` |
| *url* | **Type**: string  Значение.  *Min length:* `0`  *Max length:* `16384`  *Example:* `example` |

**Example**

```
{
  "type": "example",
  "url": "example"
}
```

### UrlGoal

Посещение страниц.
Достигается, когда выполняется хотя бы одно из условий.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *conditions* | **Type**: UrlGoalCondition[] **Example** ``` [   {     "type": "example",     "url": "example"   } ] ``` |

  **Example**

  ```
  {
    "conditions": [
      {
        "type": "example",
        "url": "example"
      }
    ]
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example",
  "conditions": [
    {
      "type": "example",
      "url": "example"
    }
  ]
}
```

### VisitDurationGoal

Продолжительность визита.
Цель будет достигнута при времени визита больше заданного.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *duration* | **Type**: integer  Продолжительность визита в секундах.  *Min value:* `1` |

  **Example**

  ```
  {
    "duration": 1
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example",
  "duration": 1
}
```

Предыдущая

Удаление цели

Следующая

Создание цели

# https://yandex.com/dev/metrika/ru/management/openapi/goal/goal

# Информация о цели

Возвращает информацию об указанной цели счетчика.

## Request

GET

```
https://api-metrika.yandex.net/management/v1/counter/{counterId}/goal/{goalId}
```

### Path parameters

|  |  |
| --- | --- |
| **Name** | **Description** |
| *counterId* | **Type**: integer  Идентификатор счетчика, информацию о цели которого вы хотите получить. |
| *goalId* | **Type**: integer  Идентификатор цели, информацию о которой вы хотите получить. |

### Query parameters

|  |  |
| --- | --- |
| **Name** | **Description** |
| *callback* | **Type**: string  Функция обратного вызова, которая обрабатывает ответ API.  *Example:* `` |

## Responses

## 200 OK

OK

### Body

application/json

```
{
  "goal": {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example",
    "conditions": [
      {}
    ]
  }
}
```

|  |  |
| --- | --- |
| **Name** | **Description** |
| *goal* | **One of 13 types** * **Type**: ActionGoal  Целевое событие. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` * **Type**: ChatGoal  Нажатие на чат. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       null     ]   }   ``` * **Type**: CompositeGoal  Составная цель.   Нужна для группировки и задания порядка обычных целей.   В качестве шагов может содержать цели типа "Посещение страниц" и "JavaScript-событие".   Шаг считается достигнутым, если были достигнуты все предыдущие шаги, и после этого были выполнены все условия текущего шага. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "steps": [       null     ]   }   ``` * **Type**: DepthGoal  Количество просмотров.   Цель считается достигнутой, если посетитель просмотрел заданное количество страниц сайта. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "depth": 2   }   ``` * **Type**: EmailGoal  Нажатие на email. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` * **Type**: FileGoal  Скачивание файлов.   Цель считается достигнутой, если посетитель скачал любой файл или определенный файл. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       null     ]   }   ``` * **Type**: MessengerGoal  Переход в мессенджер.   Цель будет достигнута при клике пользователем на ссылку, которая ведет в мессенджер. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` * **Type**: PaymentSystemGoal  Платежные системы.   Цель считается достигнутой, если посетитель совершил оплату через платежную систему. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example"   }   ``` * **Type**: PhoneGoal  Нажатие на номер телефона. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ],     "hide_phone_number": true   }   ``` * **Type**: SiteSearchGoal  Поиск по сайту.   Цель будет достигнута при поиске на сайте, если в урле в get-параметрах есть хотя бы одно совпадение. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` * **Type**: SocialNetworkGoal  Переход в социальную сеть.   Цель будет достигнута при клике пользователем на ссылку, которая ведет в социальную сеть. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       null     ]   }   ``` * **Type**: UrlGoal  Посещение страниц.   Достигается, когда выполняется хотя бы одно из условий. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` * **Type**: VisitDurationGoal  Продолжительность визита.   Цель будет достигнута при времени визита больше заданного. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "duration": 1   }   ``` **Example** ``` {   "id": 0,   "name": "example",   "type": "example",   "default_price": 0.5,   "goal_source": "example",   "is_favorite": true,   "status": "example",   "conditions": [     {       "type": "example",       "url": "example"     }   ] } ``` |

### GoalE

Информация о цели.

|  |  |
| --- | --- |
| **Name** | **Description** |
| *type* | **Type**: string  Тип цели:   * `action` — JavaScript-событие. * `chat` — клик по чату. * `email` — клик по email. * `file` — скачивание файлов. * `messenger` — переход в мессенджер. * `number` — количество просмотров. * `payment_system` — платежная система. * `phone` — клик по номеру телефона. * `search` — поиск по сайту. * `social` — переход в соцсети. * `step` — составная цель. * `url` — посещение страниц. * `visit_duration` — продолжительность визита.   *Example:* `example` |
| *default\_price* | **Type**: number  Цена цели по умолчанию. |
| *goal\_source* | **Type**: string  Признак того, как создана цель:   * `user` — цель создана пользователем Метрики. * `auto` — цель создана автоматически. К таким целям относятся т. н. автоматические цели, цель **Звонок** (создается при передаче данных о звонках).   *Example:* `example` |
| *id* | **Type**: integer  Идентификатор цели. Укажите данный параметр при изменении и удалении цели счетчика. |
| *is\_favorite* | **Type**: boolean  Является ли цель избранной:   * 0 ― не является (по умолчанию). * 1 ― является. |
| *name* | **Type**: string  Наименование цели.  *Min length:* `0`  *Max length:* `255`  *Example:* `example` |
| *status* | **Type**: string  *Example:* `example` |

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example"
}
```

### ActionGoalCondition

Список структур с условиями цели.

|  |  |
| --- | --- |
| **Name** | **Description** |
| *type* | **Type**: string  Тип условия. Возможные значения:   * `contain` — содержит. * `exact` — совпадает. * `start` — начинается с. * `regexp` — удовлетворяет регулярному выражению.   *Example:* `example` |
| *url* | **Type**: string  Значение.  *Min length:* `0`  *Max length:* `16384`  *Example:* `example` |

**Example**

```
{
  "type": "example",
  "url": "example"
}
```

### ActionGoal

Целевое событие.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *conditions* | **Type**: ActionGoalCondition[] **Example** ``` [   {     "type": "example",     "url": "example"   } ] ``` |

  **Example**

  ```
  {
    "conditions": [
      {
        "type": "example",
        "url": "example"
      }
    ]
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example",
  "conditions": [
    {
      "type": "example",
      "url": "example"
    }
  ]
}
```

### ChatGoalCondition

Список структур с условиями цели.

|  |  |
| --- | --- |
| **Name** | **Description** |
| *field* | **Type**: string  К чему применять цель:   * `chat_answered` — статус ответа в чате. * `chat_platform` — платформа чата. * `chat_tag` — тег чата.   *Example:* `example` |

**Example**

```
{
  "field": "example"
}
```

### ChatGoalConditionAnswered

**All of 2 types**

* **Type**: ChatGoalCondition

  Список структур с условиями цели.

  **Example**

  ```
  {
    "field": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *answered* | **Type**: boolean  Статус ответа в чате. |

  **Example**

  ```
  {
    "answered": true
  }
  ```

**Example**

```
{
  "field": "example",
  "answered": true
}
```

### ChatGoalConditionPlatform

**All of 2 types**

* **Type**: ChatGoalCondition

  Список структур с условиями цели.

  **Example**

  ```
  {
    "field": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *platform* | **Type**: string  Платформа чата. Возможные значения:  + `telegram`; + `viber`; + `whatsApp`. *Example:* `telegram` |

  **Example**

  ```
  {
    "platform": "telegram"
  }
  ```

**Example**

```
{
  "field": "example",
  "platform": "telegram"
}
```

### ChatGoalConditionTag

**All of 2 types**

* **Type**: ChatGoalCondition

  Список структур с условиями цели.

  **Example**

  ```
  {
    "field": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *operator* | **Type**: string  Тип условия. Возможные значения:  + `contain` — содержит. + `exact` — совпадает. + `start` — начинается с. + `regexp` — удовлетворяет регулярному выражению. *Example:* `example` |
  | *value* | **Type**: string  *Min length:* `0`  *Max length:* `1024`  *Example:* `example` |

  **Example**

  ```
  {
    "operator": "example",
    "value": "example"
  }
  ```

**Example**

```
{
  "field": "example",
  "operator": "example",
  "value": "example"
}
```

### ChatGoal

Нажатие на чат.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *conditions* | **Type**: array **One of 3 types** + **Type**: ChatGoalConditionAnswered **Example** ```   {     "field": "example",     "answered": true   }   ``` + **Type**: ChatGoalConditionPlatform **Example** ```   {     "field": "example",     "platform": "telegram"   }   ``` + **Type**: ChatGoalConditionTag **Example** ```   {     "field": "example",     "operator": "example",     "value": "example"   }   ``` **Example** ``` [   {     "field": "example",     "answered": true   } ] ``` |

  **Example**

  ```
  {
    "conditions": [
      {
        "field": "example",
        "answered": true
      }
    ]
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example",
  "conditions": [
    null
  ]
}
```

### CompositeGoal

Составная цель.
Нужна для группировки и задания порядка обычных целей.
В качестве шагов может содержать цели типа "Посещение страниц" и "JavaScript-событие".
Шаг считается достигнутым, если были достигнуты все предыдущие шаги, и после этого были выполнены все условия текущего шага.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *steps* | **Type**: array **One of 13 types** + **Type**: ActionGoal  Целевое событие. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` + **Type**: ChatGoal  Нажатие на чат. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       null     ]   }   ``` + **Type**: CompositeGoal  Составная цель.   Нужна для группировки и задания порядка обычных целей.   В качестве шагов может содержать цели типа "Посещение страниц" и "JavaScript-событие".   Шаг считается достигнутым, если были достигнуты все предыдущие шаги, и после этого были выполнены все условия текущего шага. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "steps": [       null     ]   }   ``` + **Type**: DepthGoal  Количество просмотров.   Цель считается достигнутой, если посетитель просмотрел заданное количество страниц сайта. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "depth": 2   }   ``` + **Type**: EmailGoal  Нажатие на email. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` + **Type**: FileGoal  Скачивание файлов.   Цель считается достигнутой, если посетитель скачал любой файл или определенный файл. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       null     ]   }   ``` + **Type**: MessengerGoal  Переход в мессенджер.   Цель будет достигнута при клике пользователем на ссылку, которая ведет в мессенджер. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` + **Type**: PaymentSystemGoal  Платежные системы.   Цель считается достигнутой, если посетитель совершил оплату через платежную систему. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example"   }   ``` + **Type**: PhoneGoal  Нажатие на номер телефона. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ],     "hide_phone_number": true   }   ``` + **Type**: SiteSearchGoal  Поиск по сайту.   Цель будет достигнута при поиске на сайте, если в урле в get-параметрах есть хотя бы одно совпадение. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` + **Type**: SocialNetworkGoal  Переход в социальную сеть.   Цель будет достигнута при клике пользователем на ссылку, которая ведет в социальную сеть. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       null     ]   }   ``` + **Type**: UrlGoal  Посещение страниц.   Достигается, когда выполняется хотя бы одно из условий. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {         "type": "example",         "url": "example"       }     ]   }   ``` + **Type**: VisitDurationGoal  Продолжительность визита.   Цель будет достигнута при времени визита больше заданного. **Example** ```   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "duration": 1   }   ```  *Min items:* `0`  *Max items:* `5`  *Min items:* `0`  *Max items:* `5` **Example** ``` [   {     "id": 0,     "name": "example",     "type": "example",     "default_price": 0.5,     "goal_source": "example",     "is_favorite": true,     "status": "example",     "conditions": [       {}     ]   } ] ``` |

  **Example**

  ```
  {
    "steps": [
      {
        "id": 0,
        "name": "example",
        "type": "example",
        "default_price": 0.5,
        "goal_source": "example",
        "is_favorite": true,
        "status": "example",
        "conditions": [
          null
        ]
      }
    ]
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example",
  "steps": [
    null
  ]
}
```

### DepthGoal

Количество просмотров.
Цель считается достигнутой, если посетитель просмотрел заданное количество страниц сайта.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *depth* | **Type**: integer  Количество просмотренных пользователем страниц.  *Min value:* `2` |

  **Example**

  ```
  {
    "depth": 2
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example",
  "depth": 2
}
```

### EmailGoalCondition

Список структур с условиями цели.

|  |  |
| --- | --- |
| **Name** | **Description** |
| *url* | **Type**: string  Значение.  *Min length:* `0`  *Max length:* `1024`  *Example:* `example` |
| *type* | **Type**: string  *Example:* `example` |

**Example**

```
{
  "type": "example",
  "url": "example"
}
```

### EmailGoal

Нажатие на email.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *conditions* | **Type**: EmailGoalCondition[] **Example** ``` [   {     "type": "example",     "url": "example"   } ] ``` |

  **Example**

  ```
  {
    "conditions": [
      {
        "type": "example",
        "url": "example"
      }
    ]
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example",
  "conditions": [
    {
      "type": "example",
      "url": "example"
    }
  ]
}
```

### FileGoalCondition

Список структур с условиями цели.

|  |  |
| --- | --- |
| **Name** | **Description** |
| *type* | **Type**: string  Тип условия. Возможные значения:   * `all_files` * `file`   *Example:* `example` |

**Example**

```
{
  "type": "example"
}
```

### FileGoalConditionAllFiles

**All of 1 type**

* **Type**: FileGoalCondition

  Список структур с условиями цели.

  **Example**

  ```
  {
    "type": "example"
  }
  ```

**Example**

```
{
  "type": "example"
}
```

### FileGoalConditionFile

**All of 2 types**

* **Type**: FileGoalCondition

  Список структур с условиями цели.

  **Example**

  ```
  {
    "type": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *url* | **Type**: string  Значение.  *Min length:* `0`  *Max length:* `16384`  *Example:* `example` |

  **Example**

  ```
  {
    "url": "example"
  }
  ```

**Example**

```
{
  "type": "example",
  "url": "example"
}
```

### FileGoal

Скачивание файлов.
Цель считается достигнутой, если посетитель скачал любой файл или определенный файл.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *conditions* | **Type**: array **One of 2 types** + **Type**: FileGoalConditionAllFiles **Example** ```   {     "type": "example"   }   ``` + **Type**: FileGoalConditionFile **Example** ```   {     "type": "example",     "url": "example"   }   ``` **Example** ``` [   {     "type": "example"   } ] ``` |

  **Example**

  ```
  {
    "conditions": [
      {
        "type": "example"
      }
    ]
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example",
  "conditions": [
    null
  ]
}
```

### MessengerGoalCondition

Список структур с условиями цели.

|  |  |
| --- | --- |
| **Name** | **Description** |
| *url* | **Type**: string  Значение.  *Min length:* `0`  *Max length:* `16384`  *Example:* `example` |
| *type* | **Type**: string  *Example:* `example` |

**Example**

```
{
  "type": "example",
  "url": "example"
}
```

### MessengerGoal

Переход в мессенджер.
Цель будет достигнута при клике пользователем на ссылку, которая ведет в мессенджер.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *conditions* | **Type**: MessengerGoalCondition[] **Example** ``` [   {     "type": "example",     "url": "example"   } ] ``` |

  **Example**

  ```
  {
    "conditions": [
      {
        "type": "example",
        "url": "example"
      }
    ]
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example",
  "conditions": [
    {
      "type": "example",
      "url": "example"
    }
  ]
}
```

### PaymentSystemGoal

Платежные системы.
Цель считается достигнутой, если посетитель совершил оплату через платежную систему.

**All of 1 type**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example"
}
```

### PhoneGoalCondition

Список структур с условиями цели.

|  |  |
| --- | --- |
| **Name** | **Description** |
| *url* | **Type**: string  Значение.  *Min length:* `0`  *Max length:* `25`  *Example:* `example` |
| *type* | **Type**: string  *Example:* `example` |

**Example**

```
{
  "type": "example",
  "url": "example"
}
```

### PhoneGoal

Нажатие на номер телефона.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *conditions* | **Type**: PhoneGoalCondition[] **Example** ``` [   {     "type": "example",     "url": "example"   } ] ``` |
  | *hide\_phone\_number* | **Type**: boolean  Скрывать номер телефона на десктопах. |

  **Example**

  ```
  {
    "conditions": [
      {
        "type": "example",
        "url": "example"
      }
    ],
    "hide_phone_number": true
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example",
  "conditions": [
    {
      "type": "example",
      "url": "example"
    }
  ],
  "hide_phone_number": true
}
```

### SiteSearchGoal

Поиск по сайту.
Цель будет достигнута при поиске на сайте, если в урле в get-параметрах есть хотя бы одно совпадение.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *conditions* | **Type**: SiteSearchGoalCondition[] **Example** ``` [   {     "type": "example",     "url": "example"   } ] ``` |

  **Example**

  ```
  {
    "conditions": [
      {
        "type": "example",
        "url": "example"
      }
    ]
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example",
  "conditions": [
    {
      "type": "example",
      "url": "example"
    }
  ]
}
```

### SocialNetworkGoalCondition

Список структур с условиями цели.

|  |  |
| --- | --- |
| **Name** | **Description** |
| *type* | **Type**: string  Тип условия. Возможные значения:   * `all_social` * `social`   *Example:* `example` |

**Example**

```
{
  "type": "example"
}
```

### SocialNetworkGoalConditionAllSocial

**All of 1 type**

* **Type**: SocialNetworkGoalCondition

  Список структур с условиями цели.

  **Example**

  ```
  {
    "type": "example"
  }
  ```

**Example**

```
{
  "type": "example"
}
```

### SocialNetworkGoalConditionSocial

**All of 2 types**

* **Type**: SocialNetworkGoalCondition

  Список структур с условиями цели.

  **Example**

  ```
  {
    "type": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *url* | **Type**: string  Значение.  *Min length:* `0`  *Max length:* `16384`  *Example:* `example` |

  **Example**

  ```
  {
    "url": "example"
  }
  ```

**Example**

```
{
  "type": "example",
  "url": "example"
}
```

### SocialNetworkGoal

Переход в социальную сеть.
Цель будет достигнута при клике пользователем на ссылку, которая ведет в социальную сеть.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *conditions* | **Type**: array **One of 2 types** + **Type**: SocialNetworkGoalConditionAllSocial **Example** ```   {     "type": "example"   }   ``` + **Type**: SocialNetworkGoalConditionSocial **Example** ```   {     "type": "example",     "url": "example"   }   ``` **Example** ``` [   {     "type": "example"   } ] ``` |

  **Example**

  ```
  {
    "conditions": [
      {
        "type": "example"
      }
    ]
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example",
  "conditions": [
    null
  ]
}
```

### UrlGoalCondition

Список структур с условиями цели.

|  |  |
| --- | --- |
| **Name** | **Description** |
| *type* | **Type**: string  Тип условия. Возможные значения:   * `contain` — содержит. * `exact` — совпадает. * `start` — начинается с. * `regexp` — удовлетворяет регулярному выражению. * `action` — используется в составных целях. * `regexp_action` — используется в составных целях. * `contain_action` — используется в составных целях.   *Example:* `example` |
| *url* | **Type**: string  Значение.  *Min length:* `0`  *Max length:* `16384`  *Example:* `example` |

**Example**

```
{
  "type": "example",
  "url": "example"
}
```

### UrlGoal

Посещение страниц.
Достигается, когда выполняется хотя бы одно из условий.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *conditions* | **Type**: UrlGoalCondition[] **Example** ``` [   {     "type": "example",     "url": "example"   } ] ``` |

  **Example**

  ```
  {
    "conditions": [
      {
        "type": "example",
        "url": "example"
      }
    ]
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example",
  "conditions": [
    {
      "type": "example",
      "url": "example"
    }
  ]
}
```

### VisitDurationGoal

Продолжительность визита.
Цель будет достигнута при времени визита больше заданного.

**All of 2 types**

* **Type**: GoalE

  Информация о цели.

  **Example**

  ```
  {
    "id": 0,
    "name": "example",
    "type": "example",
    "default_price": 0.5,
    "goal_source": "example",
    "is_favorite": true,
    "status": "example"
  }
  ```
* **Type**: object

  |  |  |
  | --- | --- |
  | *duration* | **Type**: integer  Продолжительность визита в секундах.  *Min value:* `1` |

  **Example**

  ```
  {
    "duration": 1
  }
  ```

**Example**

```
{
  "id": 0,
  "name": "example",
  "type": "example",
  "default_price": 0.5,
  "goal_source": "example",
  "is_favorite": true,
  "status": "example",
  "duration": 1
}
```

Предыдущая

Удаление фильтра доступа

Следующая

Изменение цели

