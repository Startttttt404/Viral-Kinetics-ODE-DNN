import pandas as pd
import numpy as np
import torch.utils.data
import torchmetrics
from torch import optim, nn
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import minmax_scale
import lightning.pytorch as pl
from lightning.pytorch.loggers import TensorBoardLogger
from lightning.pytorch.callbacks import ModelCheckpoint

if torch.cuda.is_available():
    DEVICE = 'cuda'
else:
    DEVICE = 'cpu'
BATCH_SIZE = 2048

checkpoint_callback = ModelCheckpoint(
    save_top_k=5,
    monitor="testing_loss",
    mode="min",
    filename="sample-mnist-{epoch:02d}-{testing_loss:.5f}",
)
class ViralKineticsDNN(pl.LightningModule):
    def __init__(self):
        super().__init__()
        self.stack = nn.Sequential(
            nn.Linear(6, 256, dtype=torch.float64).to(DEVICE),
            nn.ReLU(),
            nn.Dropout(0.3),
            nn.Linear(256, 512, dtype=torch.float64).to(DEVICE),
            nn.ReLU(),
            nn.Dropout(0.3),
            nn.Linear(512, 512, dtype=torch.float64).to(DEVICE),
            nn.ReLU(),
            nn.Dropout(0.3),
            nn.Linear(512, 1024, dtype=torch.float64).to(DEVICE),
            nn.ReLU(),
            nn.Dropout(0.3),
            nn.Linear(1024, 1024, dtype=torch.float64).to(DEVICE),
            nn.ReLU(),
            nn.Dropout(0.3),
            nn.Linear(1024, 6, dtype=torch.float64).to(DEVICE),
        )
        self.loss_function = nn.functional.mse_loss

    def forward(self, x):
        return self.stack(x)

    def training_step(self, batch, batch_idx):
        x, y = batch
        result = self.stack(x)
        loss = self.loss_function(result, y)
        self.log("training_loss", loss)
        return loss

    def validation_step(self, batch, batch_idx):
        x, y = batch
        result = self.stack(x)
        loss = self.loss_function(result, y)
        self.log("testing_loss", loss)

    def configure_optimizers(self):
        optimizer = optim.Adam(self.parameters(), lr=0.01)
        return optimizer


class ODEDataset(torch.utils.data.Dataset):
    def __init__(self, path):
        self.data = pd.read_csv(path)
        self.data = self.data.drop(self.data.columns[0], axis=1)

        self.data = self.data.mask(self.data < 1, 1)
        self.data['xTarget'] = np.log(self.data['xTarget'])
        self.data['xPre-Infected'] = np.log(self.data['xPre-Infected'])
        self.data['xInfected'] = np.log(self.data['xInfected'])
        self.data['xVirus'] = np.log(self.data['xVirus'])
        self.data['xCDE8e'] = np.log(self.data['xCDE8e'])
        self.data['xCD8m'] = np.log(self.data['xCD8m'])

    def __len__(self):
        return len(self.data)

    def __getitem__(self, idx):
        row = self.data.iloc[idx]
        x = torch.from_numpy(row[0:6].to_numpy()).double().to(DEVICE)
        y = torch.from_numpy(row[6:12].to_numpy()).double().to(DEVICE)
        return x, y

class ViralKineticsDNNClassifier(pl.LightningModule):
    def __init__(self):
        super().__init__()
        self.stack = nn.Sequential(
            nn.Linear(6, 512, dtype=torch.float64).to(DEVICE),
            nn.ReLU(),
            nn.Dropout(0.6),
            nn.Linear(512, 1024, dtype=torch.float64).to(DEVICE),
            nn.ReLU(),
            nn.Dropout(0.6),
            nn.Linear(1024, 4, dtype=torch.float64).to(DEVICE),
        )
        self.loss_function = nn.CrossEntropyLoss()
        self.softmax = nn.Softmax(dim=1)
        self.accuracy = torchmetrics.Accuracy(task="multiclass", num_classes=4)

    def translate(self, prediction):
        match prediction:
            case 0:
                return "Low"
            case 1:
                return "Medium-Low"
            case 2:
                return "Medium-High"
            case 3:
                return "High"

    def forward(self, x):
        y = self.softmax(self.stack(x))
        prediction = y.argmax()
        return self.translate(prediction)

    def training_step(self, batch, batch_idx):
        x, y = batch
        result = self.stack(x)
        loss = self.loss_function(result, y)
        result = self.softmax(result)
        print("Input: " + str(x[0, :]))
        print("Result: " + self.translate(result[0].argmax()))
        print("Target: " + self.translate(y[0].argmax()))
        self.log("training_loss", loss)
        return loss

    def validation_step(self, batch, batch_idx, dataloader_idx):
        x, y = batch
        result = self.stack(x)
        loss = self.loss_function(result, y)
        prediction = torch.argmax(result, dim=1)
        accuracy = self.accuracy(prediction, torch.argmax(y, dim=1).long().flatten())
        self.log("testing_loss loader: " + str(dataloader_idx), loss)
        self.log("accuracy loader: " + str(dataloader_idx), accuracy)

    def configure_optimizers(self):
        optimizer = optim.Adam(self.parameters(), lr=0.0001)
        # scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, 1000)
        # return {'optimizer': optimizer, 'lr_scheduler': {"scheduler": scheduler}}
        return optimizer

class lazy_classifier(pl.LightningModule):
    def __init__(self, value, y_max, y_min):
        super().__init__()
        self.value = value
        self.y_max = y_max
        self.y_min = y_min
        self.loss_function = nn.CrossEntropyLoss()
        self.accuracy = torchmetrics.Accuracy(task="multiclass", num_classes=4)

    def forward(self, x):
        actual_value = np.exp(x[self.value])
        return self.translate(self.bracket(actual_value))

    def training_step(self, batch, batch_idx):
        pass
    def test_step(self, batch, batch_idx, dataloader_idx):
        x, y = batch
        x = x.cpu()
        y = y.cpu()
        actual_value = np.exp(x)
        prediction = actual_value[:, self.value]
        prediction = prediction.apply_(self.bracket).long().flatten()
        one_hots = nn.functional.one_hot(prediction, num_classes=4).float()
        loss = self.loss_function(one_hots, y)
        accuracy = self.accuracy(prediction, torch.argmax(y, dim=1).long().flatten())
        self.log("testing_loss loader: " + str(dataloader_idx), loss)
        self.log("accuracy loader: " + str(dataloader_idx), accuracy)

    def bracket(self, y):
        y_mid = (self.y_min + self.y_max) / 2
        if y > y_mid:
            if y > (y_mid + self.y_max) / 2:
                return 3
            else:
                return 2
        elif y > (y_mid + self.y_min) / 2:
            return 1
        return 0

    def translate(self, prediction):
        match prediction:
            case 0:
                return "Low"
            case 1:
                return "Medium-Low"
            case 2:
                return "Medium-High"
            case 3:
                return "High"

class lazy_classifier2(pl.LightningModule):
    def __init__(self, value, y_max, y_min):
        super().__init__()
        self.value = value
        self.y_max = y_max
        self.y_min = y_min
        self.loss_function = nn.CrossEntropyLoss()
        self.accuracy = torchmetrics.Accuracy(task="multiclass", num_classes=4)

    def forward(self, x):
        actual_value = np.exp(x[self.value])
        return self.translate(self.bracket(actual_value))

    def training_step(self, batch, batch_idx):
        pass
    def test_step(self, batch, batch_idx, dataloader_idx):
        x, y = batch
        x = x.cpu()
        y = y.cpu()
        actual_value = np.exp(x)
        prediction = actual_value[:, self.value]
        prediction = prediction.apply_(self.bracket).long().flatten()
        one_hots = nn.functional.one_hot(prediction, num_classes=4).float()
        loss = self.loss_function(one_hots, y)
        accuracy = self.accuracy(prediction, torch.argmax(y, dim=1).long().flatten())
        self.log("testing_loss loader: " + str(dataloader_idx), loss)
        self.log("accuracy loader: " + str(dataloader_idx), accuracy)

    def bracket(self, y):
        y_mid = (self.y_min + self.y_max) / 2
        if y > y_mid:
            if y > (y_mid + self.y_max) / 2:
                return 3
            else:
                return 2
        elif y > (y_mid + self.y_min) / 2:
            return 1
        return 0

    def translate(self, prediction):
        match prediction:
            case 0:
                return "Medium-Low"
            case 1:
                return "Medium-High"
            case 2:
                return "High"
            case 3:
                return "High"


class ODEDatasetClassifier(torch.utils.data.Dataset):
    def __init__(self, path, atr):
        data = pd.read_csv(path)
        data = data.drop(data.columns[0], axis=1)

        data = data.mask(data < 1, 1)
        data['xTarget'] = np.log(data['xTarget'])
        data['xPre-Infected'] = np.log(data['xPre-Infected'])
        data['xInfected'] = np.log(data['xInfected'])
        data['xVirus'] = np.log(data['xVirus'])
        data['xCDE8e'] = np.log(data['xCDE8e'])
        data['xCD8m'] = np.log(data['xCD8m'])
        y_cols = ['yTarget', 'yPre-Infected', 'yInfected', 'yVirus', 'yCDE8e', 'yCD8m']
        y_cols.pop(atr)
        data = data.drop(columns=y_cols)
        self.atr = atr
        self.y_max = data.max(axis=0)[6]
        self.y_min = data.min(axis=0)[6]
        self.data = data

    def __len__(self):
        return len(self.data)

    def bracket(self, y):
        y_mid = (self.y_min + self.y_max) / 2
        if y > y_mid:
            if y > (y_mid + self.y_max) / 2:
                return 3
            else:
                return 2
        elif y > (y_mid + self.y_min) / 2:
            return 1
        return 0

    def __getitem__(self, idx):
        row = self.data.iloc[idx]
        x = torch.from_numpy(row[0:6].to_numpy()).double().to(DEVICE)
        y = row[6]
        y_tensor = torch.zeros(4).double().to(DEVICE)
        y_tensor[self.bracket(y)] = 1
        # y = torch.tensor(row[6]).double().to(DEVICE)
        return x, y_tensor

class ODEDatasetClassifierNoisy(torch.utils.data.Dataset):
    def __init__(self, path, atr):
        data = pd.read_csv(path)
        data = data.drop(data.columns[0], axis=1)

        noise = np.random.normal(0, 10000, [len(data), 12])
        data = data + noise

        data = data.mask(data < 1, 1)

        data['xTarget'] = np.log(data['xTarget'])
        data['xPre-Infected'] = np.log(data['xPre-Infected'])
        data['xInfected'] = np.log(data['xInfected'])
        data['xVirus'] = np.log(data['xVirus'])
        data['xCDE8e'] = np.log(data['xCDE8e'])
        data['xCD8m'] = np.log(data['xCD8m'])
        y_cols = ['yTarget', 'yPre-Infected', 'yInfected', 'yVirus', 'yCDE8e', 'yCD8m']
        y_cols.pop(atr)
        data = data.drop(columns=y_cols)

        # y_col = data.iloc[:, 6]
        # noise = pd.Series(np.random.normal(0, 10000, [len(data), 1]).flatten())
        # y_col = y_col + noise

        self.atr = atr
        self.y_max = data.max(axis=0)[6]
        self.y_min = data.min(axis=0)[6]
        self.data = data

    def __len__(self):
        return len(self.data)

    def bracket(self, y):
        y_mid = (self.y_min + self.y_max) / 2
        if y > y_mid:
            if y > (y_mid + self.y_max) / 2:
                return 3
            else:
                return 2
        elif y > (y_mid + self.y_min) / 2:
            return 1
        return 0

    def __getitem__(self, idx):
        row = self.data.iloc[idx]
        x = torch.from_numpy(row[0:6].to_numpy()).double().to(DEVICE)
        y = row[6]
        y_tensor = torch.zeros(4).double().to(DEVICE)
        y_tensor[self.bracket(y)] = 1
        # y = torch.tensor(row[6]).double().to(DEVICE)
        return x, y_tensor



if __name__ == '__main__':
    dataset = ODEDatasetClassifier("data/viral_kinetics_none_2_0_12.csv", 3)
    noisy_dataset = ODEDatasetClassifierNoisy("data/viral_kinetics_none_2_0_12.csv", 3)
    training_dataset, testing_dataset = torch.utils.data.random_split(dataset, [.85, .15])
    noisy_training_dataset, noisy_testing_dataset = torch.utils.data.random_split(noisy_dataset, [.85, .15])

    lazy_model = lazy_classifier(dataset.atr, dataset.y_max, dataset.y_min)
    model = ViralKineticsDNNClassifier()

    training_loader = torch.utils.data.DataLoader(training_dataset, batch_size=BATCH_SIZE, shuffle=True)
    testing_loader = torch.utils.data.DataLoader(testing_dataset, batch_size=BATCH_SIZE)
    noisy_training_loader = torch.utils.data.DataLoader(noisy_training_dataset, batch_size=BATCH_SIZE, shuffle=True)
    noisy_testing_loader = torch.utils.data.DataLoader(noisy_testing_dataset, batch_size=BATCH_SIZE)

    trainer = pl.Trainer(log_every_n_steps=1, accelerator=DEVICE, max_epochs=500,
                         logger=TensorBoardLogger("lightning_logs", name="2 Days, only V as Classifier, Lazy 85% data",
                                                  version="version_1"))
    trainer.test(model=lazy_model, dataloaders=[testing_loader, noisy_testing_loader])

    # trainer = pl.Trainer(log_every_n_steps=1, accelerator=DEVICE, max_epochs=500,
    #                      logger=TensorBoardLogger("lightning_logs", name="0.5 Day, only V as Classifier, 85% data",
    #                                               version="version_1"))
    # trainer.fit(model=model, train_dataloaders=training_loader, val_dataloaders=[testing_loader, noisy_testing_loader])

    trainer = pl.Trainer(log_every_n_steps=1, accelerator=DEVICE, max_epochs=1000,
                         logger=TensorBoardLogger("lightning_logs", name="2 Days, only V as Classifier, Noisey Trained 85% data",
                                                  version="version_1"))
    trainer.fit(model=model, train_dataloaders=noisy_training_loader,
                val_dataloaders=[testing_loader, noisy_testing_loader])

    # dataset = ODEDatasetClassifier("data/viral_kinetics_none_1_0_12.csv", 3)
    # noisy_dataset = ODEDatasetClassifierNoisy("data/viral_kinetics_none_1_0_12.csv", 3)
    # training_dataset, testing_dataset = torch.utils.data.random_split(dataset, [.75, .25])
    # noisy_training_dataset, noisy_testing_dataset = torch.utils.data.random_split(noisy_dataset, [.75, .25])
    #
    # lazy_model = lazy_classifier(dataset.atr, dataset.y_max, dataset.y_min)
    # model = ViralKineticsDNNClassifier()
    #
    # training_loader = torch.utils.data.DataLoader(training_dataset, batch_size=BATCH_SIZE, shuffle=True)
    # testing_loader = torch.utils.data.DataLoader(testing_dataset, batch_size=BATCH_SIZE)
    # noisy_training_loader = torch.utils.data.DataLoader(noisy_training_dataset, batch_size=BATCH_SIZE, shuffle=True)
    # noisy_testing_loader = torch.utils.data.DataLoader(noisy_testing_dataset, batch_size=BATCH_SIZE)
    #
    # trainer = pl.Trainer(log_every_n_steps=1, accelerator=DEVICE, max_epochs=500,
    #                      logger=TensorBoardLogger("lightning_logs", name="1 Day, only V as Classifier, Lazy 75% data", version="version_1"))
    # trainer.test(model=lazy_model, dataloaders=[testing_loader, noisy_testing_loader])
    #
    # trainer = pl.Trainer(log_every_n_steps=1, accelerator=DEVICE, max_epochs=500,
    #                      logger=TensorBoardLogger("lightning_logs", name="1 Day, only V as Classifier, 75% data", version="version_1"))
    # trainer.fit(model=model, train_dataloaders=training_loader, val_dataloaders=[testing_loader, noisy_testing_loader])
    #
    # trainer = pl.Trainer(log_every_n_steps=1, accelerator=DEVICE, max_epochs=500,
    #                      logger=TensorBoardLogger("lightning_logs", name="1 Day, only V as Classifier, Noisy Trained 75% data",
    #                                               version="version_1"))
    # trainer.fit(model=model, train_dataloaders=noisy_training_loader, val_dataloaders=[testing_loader, noisy_testing_loader])
    #
    #
    # dataset = ODEDatasetClassifier("data/viral_kinetics_none_1_0_12.csv", 3)
    # noisy_dataset = ODEDatasetClassifierNoisy("data/viral_kinetics_none_1_0_12.csv", 3)
    # training_dataset, testing_dataset = torch.utils.data.random_split(dataset, [.50, .50])
    # noisy_training_dataset, noisy_testing_dataset = torch.utils.data.random_split(noisy_dataset, [.50, .50])
    #
    # lazy_model = lazy_classifier(dataset.atr, dataset.y_max, dataset.y_min)
    # model = ViralKineticsDNNClassifier()
    #
    # training_loader = torch.utils.data.DataLoader(training_dataset, batch_size=BATCH_SIZE, shuffle=True)
    # testing_loader = torch.utils.data.DataLoader(testing_dataset, batch_size=BATCH_SIZE)
    # noisy_training_loader = torch.utils.data.DataLoader(noisy_training_dataset, batch_size=BATCH_SIZE, shuffle=True)
    # noisy_testing_loader = torch.utils.data.DataLoader(noisy_testing_dataset, batch_size=BATCH_SIZE)
    #
    # trainer = pl.Trainer(log_every_n_steps=1, accelerator=DEVICE, max_epochs=500,
    #                      logger=TensorBoardLogger("lightning_logs", name="1 Day, only V as Classifier, Lazy 50% data",
    #                                               version="version_1"))
    # trainer.test(model=lazy_model, dataloaders=[testing_loader, noisy_testing_loader])
    #
    # trainer = pl.Trainer(log_every_n_steps=1, accelerator=DEVICE, max_epochs=500,
    #                      logger=TensorBoardLogger("lightning_logs", name="1 Day, only V as Classifier, 50% data",
    #                                               version="version_1"))
    # trainer.fit(model=model, train_dataloaders=training_loader, val_dataloaders=[testing_loader, noisy_testing_loader])
    #
    # trainer = pl.Trainer(log_every_n_steps=1, accelerator=DEVICE, max_epochs=500,
    #                      logger=TensorBoardLogger("lightning_logs", name="1 Day, only V as Classifier, Noisy Trained 50% data",
    #                                               version="version_1"))
    # trainer.fit(model=model, train_dataloaders=noisy_training_loader,
    #             val_dataloaders=[testing_loader, noisy_testing_loader])
    #
    # dataset = ODEDatasetClassifier("data/viral_kinetics_none_1_0_12.csv", 3)
    # noisy_dataset = ODEDatasetClassifierNoisy("data/viral_kinetics_none_1_0_12.csv", 3)
    # training_dataset, testing_dataset = torch.utils.data.random_split(dataset, [.25, .75])
    # noisy_training_dataset, noisy_testing_dataset = torch.utils.data.random_split(noisy_dataset, [.25, .75])
    #
    # lazy_model = lazy_classifier(dataset.atr, dataset.y_max, dataset.y_min)
    # model = ViralKineticsDNNClassifier()
    #
    # training_loader = torch.utils.data.DataLoader(training_dataset, batch_size=BATCH_SIZE, shuffle=True)
    # testing_loader = torch.utils.data.DataLoader(testing_dataset, batch_size=BATCH_SIZE)
    # noisy_training_loader = torch.utils.data.DataLoader(noisy_training_dataset, batch_size=BATCH_SIZE, shuffle=True)
    # noisy_testing_loader = torch.utils.data.DataLoader(noisy_testing_dataset, batch_size=BATCH_SIZE)
    #
    # trainer = pl.Trainer(log_every_n_steps=1, accelerator=DEVICE, max_epochs=500,
    #                      logger=TensorBoardLogger("lightning_logs", name="1 Day, only V as Classifier, Lazy 25% data",
    #                                               version="version_1"))
    # trainer.test(model=lazy_model, dataloaders=[testing_loader, noisy_testing_loader])
    #
    # trainer = pl.Trainer(log_every_n_steps=1, accelerator=DEVICE, max_epochs=500,
    #                      logger=TensorBoardLogger("lightning_logs", name="1 Day, only V as Classifier, 25% data",
    #                                               version="version_1"))
    # trainer.fit(model=model, train_dataloaders=training_loader, val_dataloaders=[testing_loader, noisy_testing_loader])
    #
    # trainer = pl.Trainer(log_every_n_steps=1, accelerator=DEVICE, max_epochs=500,
    #                      logger=TensorBoardLogger("lightning_logs", name="1 Day, only V as Classifier, Noisy Trained 25% data",
    #                                               version="version_1"))
    # trainer.fit(model=model, train_dataloaders=noisy_training_loader,
    #             val_dataloaders=[testing_loader, noisy_testing_loader])

    # dataset = ODEDataset("data/viral_kinetics_none_1_0_12.csv")
    # training_dataset, testing_dataset = torch.utils.data.random_split(dataset, [.85, .15])
    # model = ViralKineticsDNN()
    # training_loader = torch.utils.data.DataLoader(training_dataset, batch_size=BATCH_SIZE, shuffle=True)
    # testing_loader = torch.utils.data.DataLoader(testing_dataset, batch_size=BATCH_SIZE)
    #
    # trainer = pl.Trainer(log_every_n_steps=1, accelerator=DEVICE, max_epochs=500,
    #                      logger=TensorBoardLogger("lightning_logs", name="1 Day-0 to 12 Model", version="version_1"))
    # trainer.fit(model=model, train_dataloaders=training_loader, val_dataloaders=testing_loader)

    # dataset = ODEDatasetClassifierNoisy("data/viral_kinetics_delta_eta_d_e_1_1_0_12.csv", 3)
    # training_dataset, testing_dataset = torch.utils.data.random_split(dataset, [.85, .15])
    # model = ViralKineticsDNNClassifier()
    # training_loader = torch.utils.data.DataLoader(training_dataset, batch_size=BATCH_SIZE, shuffle=True)
    # testing_loader = torch.utils.data.DataLoader(testing_dataset, batch_size=BATCH_SIZE)
    #
    # trainer = pl.Trainer(log_every_n_steps=1, accelerator=DEVICE, max_epochs=200,
    #                      logger=TensorBoardLogger("lightning_logs",
    #                                               name="1 Day, only V as Classifier, Noisy, Varied System Variables",
    #                                               version="version_1"))
    # trainer.fit(model=model, train_dataloaders=training_loader, val_dataloaders=testing_loader)


    # dataset = ODEDatasetClassifier("data/viral_kinetics_none_0.01_0_12.csv", 0)
    # training_dataset, testing_dataset = torch.utils.data.random_split(dataset, [.85, .15])
    # model = ViralKineticsDNNClassifier()
    # training_loader = torch.utils.data.DataLoader(training_dataset, batch_size=BATCH_SIZE, shuffle=True)
    # testing_loader = torch.utils.data.DataLoader(testing_dataset, batch_size=BATCH_SIZE)
    #
    # trainer = pl.Trainer(log_every_n_steps=1, accelerator=DEVICE, max_epochs=2000,
    #                      logger=TensorBoardLogger("lightning_logs", name="0.01-0 to 12 Model, only T as Classifier", version="version_1"))
    # trainer.fit(model=model, train_dataloaders=training_loader, val_dataloaders=testing_loader)
    #
    # dataset = ODEDatasetClassifier("data/viral_kinetics_none_0.01_0_12.csv", 1)
    # training_dataset, testing_dataset = torch.utils.data.random_split(dataset, [.85, .15])
    # model = ViralKineticsDNNClassifier()
    # training_loader = torch.utils.data.DataLoader(training_dataset, batch_size=BATCH_SIZE, shuffle=True)
    # testing_loader = torch.utils.data.DataLoader(testing_dataset, batch_size=BATCH_SIZE)
    #
    # trainer = pl.Trainer(log_every_n_steps=1, accelerator=DEVICE, max_epochs=2000,
    #                      logger=TensorBoardLogger("lightning_logs", name="0.01-0 to 12 Model, only P_I as Classifier",
    #                                               version="version_1"))
    # trainer.fit(model=model, train_dataloaders=training_loader, val_dataloaders=testing_loader)
    #
    # dataset = ODEDatasetClassifier("data/viral_kinetics_none_0.01_0_12.csv", 2)
    # training_dataset, testing_dataset = torch.utils.data.random_split(dataset, [.85, .15])
    # model = ViralKineticsDNNClassifier()
    # training_loader = torch.utils.data.DataLoader(training_dataset, batch_size=BATCH_SIZE, shuffle=True)
    # testing_loader = torch.utils.data.DataLoader(testing_dataset, batch_size=BATCH_SIZE)
    #
    # trainer = pl.Trainer(log_every_n_steps=1, accelerator=DEVICE, max_epochs=2000,
    #                      logger=TensorBoardLogger("lightning_logs", name="0.01-0 to 12 Model, only I as Classifier",
    #                                               version="version_1"))
    # trainer.fit(model=model, train_dataloaders=training_loader, val_dataloaders=testing_loader)
    #
    # dataset = ODEDatasetClassifier("data/viral_kinetics_none_0.01_0_12.csv", 3)
    # training_dataset, testing_dataset = torch.utils.data.random_split(dataset, [.85, .15])
    # model = ViralKineticsDNNClassifier()
    # training_loader = torch.utils.data.DataLoader(training_dataset, batch_size=BATCH_SIZE, shuffle=True)
    # testing_loader = torch.utils.data.DataLoader(testing_dataset, batch_size=BATCH_SIZE)
    #
    # trainer = pl.Trainer(log_every_n_steps=1, accelerator=DEVICE, max_epochs=2000,
    #                      logger=TensorBoardLogger("lightning_logs", name="0.01-0 to 12 Model, only V as Classifier",
    #                                               version="version_1"))
    # trainer.fit(model=model, train_dataloaders=training_loader, val_dataloaders=testing_loader)
    #
    # dataset = ODEDatasetClassifier("data/viral_kinetics_none_0.01_0_12.csv", 4)
    # training_dataset, testing_dataset = torch.utils.data.random_split(dataset, [.85, .15])
    # model = ViralKineticsDNNClassifier()
    # training_loader = torch.utils.data.DataLoader(training_dataset, batch_size=BATCH_SIZE, shuffle=True)
    # testing_loader = torch.utils.data.DataLoader(testing_dataset, batch_size=BATCH_SIZE)
    #
    # trainer = pl.Trainer(log_every_n_steps=1, accelerator=DEVICE, max_epochs=2000,
    #                      logger=TensorBoardLogger("lightning_logs", name="0.01-0 to 12 Model, only E as Classifier",
    #                                               version="version_1"))
    # trainer.fit(model=model, train_dataloaders=training_loader, val_dataloaders=testing_loader)
    #
    # dataset = ODEDatasetClassifier("data/viral_kinetics_none_0.01_0_12.csv", 5)
    # training_dataset, testing_dataset = torch.utils.data.random_split(dataset, [.85, .15])
    # model = ViralKineticsDNNClassifier()
    # training_loader = torch.utils.data.DataLoader(training_dataset, batch_size=BATCH_SIZE, shuffle=True)
    # testing_loader = torch.utils.data.DataLoader(testing_dataset, batch_size=BATCH_SIZE)
    #
    # trainer = pl.Trainer(log_every_n_steps=1, accelerator=DEVICE, max_epochs=2000,
    #                      logger=TensorBoardLogger("lightning_logs", name="0.01-0 to 12 Model, only E_M as Classifier",
    #                                               version="version_1"))
    # trainer.fit(model=model, train_dataloaders=training_loader, val_dataloaders=testing_loader)


    # dataset = ODEDatasetClassifierNoisy("data/viral_kinetics_none_0.01_0_12.csv", 0)
    # training_dataset, testing_dataset = torch.utils.data.random_split(dataset, [.85, .15])
    # model = ViralKineticsDNNClassifier()
    # training_loader = torch.utils.data.DataLoader(training_dataset, batch_size=BATCH_SIZE, shuffle=True)
    # testing_loader = torch.utils.data.DataLoader(testing_dataset, batch_size=BATCH_SIZE)
    #
    # trainer = pl.Trainer(log_every_n_steps=1, accelerator=DEVICE, max_epochs=2000,
    #                      logger=TensorBoardLogger("lightning_logs", name="0.01-0 to 12 Model, only T as Classifier, Noisy Testing", version="version_1"))
    # trainer.fit(model=model, train_dataloaders=training_loader, val_dataloaders=testing_loader)
    #
    # dataset = ODEDatasetClassifierNoisy("data/viral_kinetics_none_0.01_0_12.csv", 1)
    # training_dataset, testing_dataset = torch.utils.data.random_split(dataset, [.85, .15])
    # model = ViralKineticsDNNClassifier()
    # training_loader = torch.utils.data.DataLoader(training_dataset, batch_size=BATCH_SIZE, shuffle=True)
    # testing_loader = torch.utils.data.DataLoader(testing_dataset, batch_size=BATCH_SIZE)
    #
    # trainer = pl.Trainer(log_every_n_steps=1, accelerator=DEVICE, max_epochs=2000,
    #                      logger=TensorBoardLogger("lightning_logs", name="0.01-0 to 12 Model, only P_I as Classifier, Noisy Testing",
    #                                               version="version_1"))
    # trainer.fit(model=model, train_dataloaders=training_loader, val_dataloaders=testing_loader)
    #
    # dataset = ODEDatasetClassifierNoisy("data/viral_kinetics_none_0.01_0_12.csv", 2)
    # training_dataset, testing_dataset = torch.utils.data.random_split(dataset, [.85, .15])
    # model = ViralKineticsDNNClassifier()
    # training_loader = torch.utils.data.DataLoader(training_dataset, batch_size=BATCH_SIZE, shuffle=True)
    # testing_loader = torch.utils.data.DataLoader(testing_dataset, batch_size=BATCH_SIZE)
    #
    # trainer = pl.Trainer(log_every_n_steps=1, accelerator=DEVICE, max_epochs=2000,
    #                      logger=TensorBoardLogger("lightning_logs", name="0.01-0 to 12 Model, only I as Classifier, Noisy Testing",
    #                                               version="version_1"))
    # trainer.fit(model=model, train_dataloaders=training_loader, val_dataloaders=testing_loader)
    #
    # dataset = ODEDatasetClassifierNoisy("data/viral_kinetics_none_0.01_0_12.csv", 3)
    # training_dataset, testing_dataset = torch.utils.data.random_split(dataset, [.85, .15])
    # model = ViralKineticsDNNClassifier()
    # training_loader = torch.utils.data.DataLoader(training_dataset, batch_size=BATCH_SIZE, shuffle=True)
    # testing_loader = torch.utils.data.DataLoader(testing_dataset, batch_size=BATCH_SIZE)
    #
    # trainer = pl.Trainer(log_every_n_steps=1, accelerator=DEVICE, max_epochs=2000,
    #                      logger=TensorBoardLogger("lightning_logs", name="0.01-0 to 12 Model, only V as Classifier, Noisy Testing",
    #                                               version="version_1"))
    # trainer.fit(model=model, train_dataloaders=training_loader, val_dataloaders=testing_loader)

    # dataset = ODEDatasetClassifierNoisy("data/viral_kinetics_none_0.01_0_12.csv", 4)
    # training_dataset, testing_dataset = torch.utils.data.random_split(dataset, [.85, .15])
    # model = ViralKineticsDNNClassifier()
    # training_loader = torch.utils.data.DataLoader(training_dataset, batch_size=BATCH_SIZE, shuffle=True)
    # testing_loader = torch.utils.data.DataLoader(testing_dataset, batch_size=BATCH_SIZE)
    #
    # trainer = pl.Trainer(log_every_n_steps=1, accelerator=DEVICE, max_epochs=2000,
    #                      logger=TensorBoardLogger("lightning_logs", name="0.01-0 to 12 Model, only E as Classifier, Noisy Testing",
    #                                               version="version_1"))
    # trainer.fit(model=model, train_dataloaders=training_loader, val_dataloaders=testing_loader)
    #
    # dataset = ODEDatasetClassifierNoisy("data/viral_kinetics_none_0.01_0_12.csv", 5)
    # training_dataset, testing_dataset = torch.utils.data.random_split(dataset, [.85, .15])
    # model = ViralKineticsDNNClassifier()
    # training_loader = torch.utils.data.DataLoader(training_dataset, batch_size=BATCH_SIZE, shuffle=True)
    # testing_loader = torch.utils.data.DataLoader(testing_dataset, batch_size=BATCH_SIZE)
    #
    # trainer = pl.Trainer(log_every_n_steps=1, accelerator=DEVICE, max_epochs=2000,
    #                      logger=TensorBoardLogger("lightning_logs", name="0.01-0 to 12 Model, only E_M as Classifier, Noisy Testing",
    #                                               version="version_1"))
    # trainer.fit(model=model, train_dataloaders=training_loader, val_dataloaders=testing_loader)

    # dataset0 = ODEDataset("data/viral_kinetics_none_0.01_0_12.csv")
    # dataset1 = ODEDataset("data/viral_kinetics_none_0.01_0_10.csv")
    # dataset2 = ODEDataset("data/viral_kinetics_none_0.01_0_8.csv")
    # dataset3 = ODEDataset("data/viral_kinetics_none_0.01_0_6.csv")
    # dataset4 = ODEDataset("data/viral_kinetics_none_0.01_2_12.csv")
    # dataset5 = ODEDataset("data/viral_kinetics_none_0.01_2_10.csv")
    # dataset6 = ODEDataset("data/viral_kinetics_none_0.01_2_8.csv")
    # dataset7 = ODEDataset("data/viral_kinetics_none_0.01_2_6.csv")
    #
    # training_dataset0, testing_dataset0 = torch.utils.data.random_split(dataset0, [.85, .15])
    # training_dataset1, testing_dataset1 = torch.utils.data.random_split(dataset1, [.85, .15])
    # training_dataset2, testing_dataset2 = torch.utils.data.random_split(dataset2, [.85, .15])
    # training_dataset3, testing_dataset3 = torch.utils.data.random_split(dataset3, [.85, .15])
    # training_dataset4, testing_dataset4 = torch.utils.data.random_split(dataset4, [.85, .15])
    # training_dataset5, testing_dataset5 = torch.utils.data.random_split(dataset5, [.85, .15])
    # training_dataset6, testing_dataset6 = torch.utils.data.random_split(dataset6, [.85, .15])
    # training_dataset7, testing_dataset7 = torch.utils.data.random_split(dataset7, [.85, .15])
    #
    # training_loader0 = torch.utils.data.DataLoader(training_dataset0, batch_size=BATCH_SIZE, shuffle=True)
    # testing_loader0 = torch.utils.data.DataLoader(testing_dataset0, batch_size=BATCH_SIZE)
    # training_loader1 = torch.utils.data.DataLoader(training_dataset1, batch_size=BATCH_SIZE, shuffle=True)
    # testing_loader1 = torch.utils.data.DataLoader(testing_dataset1, batch_size=BATCH_SIZE)
    # training_loader2 = torch.utils.data.DataLoader(training_dataset2, batch_size=BATCH_SIZE, shuffle=True)
    # testing_loader2 = torch.utils.data.DataLoader(testing_dataset2, batch_size=BATCH_SIZE)
    # training_loader3 = torch.utils.data.DataLoader(training_dataset3, batch_size=BATCH_SIZE, shuffle=True)
    # testing_loader3 = torch.utils.data.DataLoader(testing_dataset3, batch_size=BATCH_SIZE)
    # training_loader4 = torch.utils.data.DataLoader(training_dataset4, batch_size=BATCH_SIZE, shuffle=True)
    # testing_loader4 = torch.utils.data.DataLoader(testing_dataset4, batch_size=BATCH_SIZE)
    # training_loader5 = torch.utils.data.DataLoader(training_dataset5, batch_size=BATCH_SIZE, shuffle=True)
    # testing_loader5 = torch.utils.data.DataLoader(testing_dataset5, batch_size=BATCH_SIZE)
    # training_loader6 = torch.utils.data.DataLoader(training_dataset6, batch_size=BATCH_SIZE, shuffle=True)
    # testing_loader6 = torch.utils.data.DataLoader(testing_dataset6, batch_size=BATCH_SIZE)
    # training_loader7 = torch.utils.data.DataLoader(training_dataset7, batch_size=BATCH_SIZE, shuffle=True)
    # testing_loader7 = torch.utils.data.DataLoader(testing_dataset7, batch_size=BATCH_SIZE)
    #
    # model0 = ViralKineticsDNN()
    # model1 = ViralKineticsDNN()
    # model2 = ViralKineticsDNN()
    # model3 = ViralKineticsDNN()
    # model4 = ViralKineticsDNN()
    # model5 = ViralKineticsDNN()
    # model6 = ViralKineticsDNN()
    # model7 = ViralKineticsDNN()
    #
    # trainer = pl.Trainer(log_every_n_steps=1, accelerator=DEVICE, max_epochs=10000, logger=TensorBoardLogger("lightning_logs", name="0.01-0 to 12 Model", version="version_2"))
    # trainer.fit(model=model0, train_dataloaders=training_loader0, val_dataloaders=testing_loader0)
    # trainer = pl.Trainer(log_every_n_steps=1, accelerator=DEVICE, max_epochs=10000, logger=TensorBoardLogger("lightning_logs", name="0.01-0 to 10 Model", version="version_2"))
    # trainer.fit(model=model1, train_dataloaders=training_loader1, val_dataloaders=testing_loader1)
    # trainer = pl.Trainer(log_every_n_steps=1, accelerator=DEVICE, max_epochs=10000, logger=TensorBoardLogger("lightning_logs", name="0.01-0 to 8 Model", version="version_2"))
    # trainer.fit(model=model2, train_dataloaders=training_loader2, val_dataloaders=testing_loader2)
    # trainer = pl.Trainer(log_every_n_steps=1, accelerator=DEVICE, max_epochs=10000, logger=TensorBoardLogger("lightning_logs", name="0.01-0 to 6 Model", version="version_2"))
    # trainer.fit(model=model3, train_dataloaders=training_loader3, val_dataloaders=testing_loader3)
    # trainer = pl.Trainer(log_every_n_steps=1, accelerator=DEVICE, max_epochs=10000, logger=TensorBoardLogger("lightning_logs", name="0.01-2 to 12 Model", version="version_2"))
    # trainer.fit(model=model4, train_dataloaders=training_loader4, val_dataloaders=testing_loader4)
    # trainer = pl.Trainer(log_every_n_steps=1, accelerator=DEVICE, max_epochs=10000, logger=TensorBoardLogger("lightning_logs", name="0.01-2 to 10 Model", version="version_2"))
    # trainer.fit(model=model5, train_dataloaders=training_loader5, val_dataloaders=testing_loader5)
    # trainer = pl.Trainer(log_every_n_steps=1, accelerator=DEVICE, max_epochs=10000, logger=TensorBoardLogger("lightning_logs", name="0.01-2 to 8 Model", version="version_2"))
    # trainer.fit(model=model6, train_dataloaders=training_loader6, val_dataloaders=testing_loader6)
    # rainer = pl.Trainer(log_every_n_steps=1, accelerator=DEVICE, max_epochs=10000, logger=TensorBoardLogger("lightning_logs", name="0.01-2 to 6 Model", version="version_2"))
    # trainer.fit(model=model7, train_dataloaders=training_loader7, val_dataloaders=testing_loader7)

    # dataset1 = ODEDataset("data/viral_kinetics_none_0.01_0_12.csv")
    # dataset2 = ODEDataset("data/viral_kinetics_none_0.001_0_12.csv")
    # dataset3 = ODEDataset("data/viral_kinetics_none_0.0001_0_12.csv")

    # training_dataset1, testing_dataset1 = torch.utils.data.random_split(dataset1, [.85, .15])
    # training_dataset2, testing_dataset2 = torch.utils.data.random_split(dataset2, [.85, .15])
    # training_dataset3, testing_dataset3 = torch.utils.data.random_split(dataset3, [.85, .15])
    #
    # training_loader1 = torch.utils.data.DataLoader(training_dataset1, batch_size=BATCH_SIZE, shuffle=True)
    # testing_loader1 = torch.utils.data.DataLoader(testing_dataset1, batch_size=BATCH_SIZE)
    # training_loader2 = torch.utils.data.DataLoader(training_dataset2, batch_size=BATCH_SIZE, shuffle=True)
    # testing_loader2 = torch.utils.data.DataLoader(testing_dataset2, batch_size=BATCH_SIZE)
    # training_loader3 = torch.utils.data.DataLoader(training_dataset3, batch_size=BATCH_SIZE, shuffle=True)
    # testing_loader3 = torch.utils.data.DataLoader(testing_dataset3, batch_size=BATCH_SIZE)
    #
    # model1 = ViralKineticsDNN()
    # model2 = ViralKineticsDNN()
    # model3 = ViralKineticsDNN()

    #trainer = pl.Trainer(log_every_n_steps=1, accelerator=DEVICE, max_epochs=10000, callbacks=[checkpoint_callback], logger=TensorBoardLogger("lightning_logs", name="0.01 to 0.01 Model", version="version_1"))
    #trainer.fit(model=model1, train_dataloaders=training_loader1, val_dataloaders=testing_loader1)
    #trainer = pl.Trainer(log_every_n_steps=10, accelerator=DEVICE, max_epochs=1000, callbacks=[checkpoint_callback], logger=TensorBoardLogger("lightning_logs", name="0.001 to 0.001 Model", version="version_1"))
    #trainer.fit(model=model2, train_dataloaders=training_loader2, val_dataloaders=testing_loader2)
    #trainer = pl.Trainer(log_every_n_steps=100, accelerator=DEVICE, max_epochs=100, callbacks=[checkpoint_callback], logger=TensorBoardLogger("lightning_logs", name="0.0001 to 0.0001 Model", version="version_1"))
    #trainer.fit(model=model3, train_dataloaders=training_loader3, val_dataloaders=testing_loader3)
